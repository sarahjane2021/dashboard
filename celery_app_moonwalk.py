from celery import Celery
import pandas as pd
from sqlalchemy import create_engine, MetaData, select, inspect, text, Table
from sqlalchemy.dialects.postgresql import insert
from celery.schedules import crontab
import redis, logging, os, re
import json
import hashlib
from sqlalchemy.types import Time

# ----- Setup Logging -----
LOG_FILE = "celery.log"
if not os.path.exists(LOG_FILE):
    open(LOG_FILE, "w").close()
logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logger.info("Celery logging initialized.")

# ----- Configure Celery -----
celery_app = Celery('tasks',
    broker='redis://localhost:6379/0',
    backend='redis://localhost:6379/1'
)

# ----- Redis & DB Connections -----
redis_client = redis.Redis(host="localhost", port=6379, db=2, decode_responses=True)
SRC_DATABASE_URL = "postgresql://admin:password@209.38.56.184:5432/moonwalk"
src_engine = create_engine(SRC_DATABASE_URL)
TARGET_DATABASE_URL = "postgresql://admin:password@209.38.56.184:5432/postgres"
target_engine = create_engine(TARGET_DATABASE_URL)
# ----- Redis & DB Connections -----

def table_exists(engine, table_name):
    return inspect(engine).has_table(table_name)

def get_last_processed_key(silver_table, key_col):
    try:
        query = f"SELECT MAX({key_col}) as max_key FROM {silver_table}"
        result = pd.read_sql_query(query, target_engine)
        return result['max_key'].iloc[0]
    except Exception as e:
        logger.error(f"Error fetching last processed key from {silver_table}: {e}")
        return None
    
    
###########################################
# ----- Ingestion Task -----
# 🔹 Database Ingestion Task
def safe_stringify(cell):
    """Safely stringify any cell, including lists/dicts."""
    try:
        if isinstance(cell, (dict, list)):
            return json.dumps(cell, sort_keys=True)
        return str(cell)
    except Exception:
        return str(cell)

def compute_row_hash(df):
    """Compute a hash for each row."""
    return df.apply(
        lambda row: hashlib.md5("".join([safe_stringify(cell) for cell in row.values]).encode()).hexdigest(),
        axis=1
    )

@celery_app.task(name="tasks.ingest_data")
def ingest_data():
    try:
        logger.info("🔹 Starting database ingestion task")

        # Reflect source and target schemas
        src_metadata = MetaData()
        src_metadata.reflect(bind=src_engine)
        src_tables = src_metadata.tables

        target_metadata = MetaData()
        target_metadata.reflect(bind=target_engine)

        for table_name, table_obj in src_tables.items():
            logger.info(f"🔍 Processing table: {table_name}")
            try:
                # Load source data into a DataFrame
                df_src = pd.read_sql_table(table_name, src_engine)
                df_src.columns = [re.sub(r'\W|^(?=\d)', '_', col) for col in df_src.columns]

                if df_src.empty:
                    logger.info(f"🚫 Table '{table_name}' has no data. Skipping.")
                    continue

                # Compute row hashes for source data
                df_src['_row_hash'] = compute_row_hash(df_src)

                # Check if table exists in target DB
                if table_exists(target_engine, table_name):
                    logger.info(f"📦 Table '{table_name}' exists in target DB. Checking for changes...")
                    try:
                        # Load target data into a DataFrame
                        df_target = pd.read_sql_table(table_name, target_engine)
                        df_target.columns = [re.sub(r'\W|^(?=\d)', '_', col) for col in df_target.columns]

                        # Compute row hashes for target data
                        df_target['_row_hash'] = compute_row_hash(df_target)

                        # Find new or changed rows by comparing hashes
                        new_or_changed = df_src[~df_src['_row_hash'].isin(df_target['_row_hash'])].drop(columns=['_row_hash'])

                        if not new_or_changed.empty:
                            # Ensure no duplicates before inserting
                            combined_data = pd.concat([df_target, new_or_changed]).drop_duplicates(subset=['_row_hash'], keep='first')

                            # If there are new or changed rows, insert them
                            if len(combined_data) > len(df_target):  # If data is different
                                new_or_changed.drop(columns=['_row_hash']).to_sql(
                                    table_name,
                                    target_engine,
                                    if_exists='append',
                                    index=False
                                )
                                logger.info(f"✅ Updated table '{table_name}' with {len(new_or_changed)} new/changed rows.")
                            else:
                                logger.info(f"🚫 No new data for table '{table_name}'. No changes made.")

                        else:
                            logger.info(f"🚫 No new data for table '{table_name}'. No changes made.")

                    except Exception as compare_error:
                        logger.warning(f"⚠️ Couldn't compare '{table_name}', falling back to full replace: {compare_error}")
                        df_src.drop(columns=['_row_hash']).to_sql(
                            table_name,
                            target_engine,
                            if_exists='replace',
                            index=False
                        )
                        logger.info(f"🔁 Replaced entire table '{table_name}' due to comparison failure.")
                else:
                    logger.info(f"🆕 Table '{table_name}' does not exist. Creating and inserting all data.")
                    df_src.drop(columns=['_row_hash']).to_sql(
                        table_name,
                        target_engine,
                        if_exists='replace',
                        index=False
                    )

            except Exception as table_error:
                logger.error(f"❌ Failed processing {table_name}: {table_error}")

    except Exception as e:
        logger.error(f"🚨 Ingestion task failed: {e}")


###########################################
# preprocess the leave table
def preprocess_leave():
    try:
        logger.info("🔹 Preprocessing leave table")
        src_table = "leave"
        silver_table = "silver_leave"

        # Load data from source leave table
        df = pd.read_sql_table(src_table, target_engine)

        # Drop rows where deleted_at is NOT null
        if 'deleted_at' in df.columns:
            df = df[df['deleted_at'].isnull()]

        # Convert relevant datetime columns to dates only
        for col in ['created_at', 'updated_at']:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col]).dt.date

        # Split duration_start and duration_end into date + time
        if 'duration_start' in df.columns:
            df['duration_start_date'] = pd.to_datetime(df['duration_start']).dt.date
            df['duration_start_time'] = pd.to_datetime(df['duration_start']).dt.time

        if 'duration_end' in df.columns:
            df['duration_end_date'] = pd.to_datetime(df['duration_end']).dt.date
            df['duration_end_time'] = pd.to_datetime(df['duration_end']).dt.time

        # Drop the original duration_start and duration_end if split versions exist
        df.drop(columns=['duration_start', 'duration_end'], inplace=True, errors='ignore')

        # Generate a content-based hash for each row
        df['_row_hash'] = df.astype(str).apply(lambda row: hashlib.md5(row.to_string().encode()).hexdigest(), axis=1)

        # Check for existing silver table
        if table_exists(target_engine, silver_table):
            df_silver = pd.read_sql_table(silver_table, target_engine)

            if '_row_hash' in df_silver.columns:
                # Remove rows that already exist based on hash
                df = df[~df['_row_hash'].isin(df_silver['_row_hash'])]
            else:
                logger.warning(f"⚠️ '_row_hash' not found in {silver_table}. Skipping comparison.")

        # If no new or changed data, skip insert
        if df.empty:
            logger.info(f"✅ No new or changed rows to insert into '{silver_table}'")
            return

        # Insert new rows into the silver table
        df.to_sql(silver_table, target_engine, if_exists='append', index=False)
        logger.info(f"✅ Inserted {len(df)} new/changed rows into '{silver_table}'")

    except Exception as e:
        logger.error(f"❌ Failed preprocessing leave table: {e}")


###########################################
# preprocess the employee table
def preprocess_employee():
    try:
        logger.info("🔹 Preprocessing employee table")
        src_table = "employee"
        silver_table = "silver_employee"

        # Load source data from employee table
        df = pd.read_sql_table(src_table, target_engine)

        # Drop rows where deleted_at is not null
        if 'deleted_at' in df.columns:
            df = df[df['deleted_at'].isnull()]

        # Extract date from created_at and updated_at
        for col in ['created_at', 'updated_at']:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col]).dt.date

        # Generate hash for each row
        df['_row_hash'] = df.astype(str).apply(lambda row: hashlib.md5(row.to_string().encode()).hexdigest(), axis=1)

        # Check if silver table exists
        if table_exists(target_engine, silver_table):
            # Load silver table
            df_silver = pd.read_sql_table(silver_table, target_engine)

            if '_row_hash' in df_silver.columns:
                # Compare hashes, keep only new/changed rows
                df = df[~df['_row_hash'].isin(df_silver['_row_hash'])]
            else:
                logger.warning(f"⚠️ '_row_hash' not found in {silver_table}. Skipping comparison.")

        if df.empty:
            logger.info(f"✅ No new or updated data for '{silver_table}'")
            return

        # Insert only new or changed data
        df.to_sql(silver_table, target_engine, if_exists='append', index=False)
        logger.info(f"✅ Inserted {len(df)} new/changed rows into '{silver_table}'")

    except Exception as e:
        logger.error(f"❌ Failed preprocessing employee table: {e}")


###########################################
# preprocess the attendance table
def preprocess_attendance():
    try:
        logger.info("🔹 Preprocessing attendance table")
        
        # Load data from source attendance table
        df = pd.read_sql_table('attendance', target_engine)

        # Drop rows where 'deleted_at' is NOT null
        if 'deleted_at' in df.columns:
            df = df[df['deleted_at'].isnull()]

        # Convert 'created_at', 'updated_at', and 'date_today' to date only
        for col in ['created_at', 'updated_at', 'date_today']:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce').dt.date

        # Convert time_in and time_out to HH:MM:SS
        def format_time(value):
            try:
                return pd.to_datetime(value).strftime('%H:%M:%S') if pd.notnull(value) else None
            except:
                return None

        if 'time_in' in df.columns:
            df['time_in'] = df['time_in'].apply(format_time)

        if 'time_out' in df.columns:
            df['time_out'] = df['time_out'].apply(format_time)

        # Drop unwanted columns
        columns_to_drop = ['photo', 'location_out_lon', 'location_out_lat', 'location_in_lon', 'location_in_lat']
        df.drop(columns=columns_to_drop, inplace=True, errors='ignore')

        # Generate a content-based hash for each row
        df['_row_hash'] = df.astype(str).apply(lambda row: hashlib.md5(row.to_string().encode()).hexdigest(), axis=1)

        # Define silver table name
        silver_table = 'silver_attendance'

        # Check if the silver table exists and process accordingly
        if table_exists(target_engine, silver_table):
            df_silver = pd.read_sql_table(silver_table, target_engine)

            if '_row_hash' in df_silver.columns:
                # Remove rows that already exist based on hash
                df = df[~df['_row_hash'].isin(df_silver['_row_hash'])]
            else:
                logger.warning(f"⚠️ '_row_hash' not found in {silver_table}. Skipping comparison.")

        # If no new or changed data, skip insert
        if df.empty:
            logger.info(f"✅ No new or changed rows to insert into '{silver_table}'")
            return

        # Insert new rows into the silver table
        df.to_sql(silver_table, target_engine, if_exists='append', index=False)
        logger.info(f"✅ Inserted {len(df)} new/changed rows into '{silver_table}'")

    except Exception as e:
        logger.error(f"❌ Failed preprocessing attendance table: {e}")

###########################################
# preprocess the organization table
def preprocess_organization():
    try:
        logger.info("🔹 Preprocessing organization table")
        src_table = "organization"
        silver_table = "silver_organization"
        pk = "org_id"

        # Load full data from source
        df = pd.read_sql_table(src_table, target_engine)

        # Drop rows where 'deleted_at' is NOT null
        if 'deleted_at' in df.columns:
            df = df[df['deleted_at'].isnull()]

        # Convert timestamp columns to date
        for col in ['created_at', 'updated_at']:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce').dt.date

        # Count working days
        def count_days(x):
            if isinstance(x, list):
                return len(x)
            elif isinstance(x, str):
                s = x.strip('{}')
                return len(s.split(',')) if s else 0
            return 0
        
        if 'working_days' in df.columns:
            df['num_working_days'] = df['working_days'].apply(count_days)

        # Generate row hash
        df['_row_hash'] = df.astype(str).apply(lambda row: hashlib.md5(row.to_string().encode()).hexdigest(), axis=1)

        # Compare with silver table if exists
        if table_exists(target_engine, silver_table):
            df_silver = pd.read_sql_table(silver_table, target_engine)
            if '_row_hash' in df_silver.columns:
                df = df[~df['_row_hash'].isin(df_silver['_row_hash'])]
            else:
                logger.warning(f"⚠️ '_row_hash' not found in {silver_table}. Proceeding with full insert.")
        
        # Exit early if no new/changed data
        if df.empty:
            logger.info(f"✅ No new or changed records to insert into '{silver_table}'")
            return "No new organization records to process."

        # Insert to silver table
        df.to_sql(silver_table, target_engine, index=False, if_exists='append')
        logger.info(f"✅ Inserted {len(df)} new/changed records into '{silver_table}'")
        return f"Inserted {len(df)} new/changed records into '{silver_table}'"

    except Exception as e:
        logger.error(f"❌ Failed preprocessing organization table: {e}")
        return f"Failed preprocessing organization table: {e}"
############################################################
def preprocess_story_point():
    """Extracts array values into separate columns and updates 'silver_story_point' table with UPSERT."""
    engine = create_engine(TARGET_DATABASE_URL)

    with engine.connect() as conn:
        # Check if 'silver_story_point' exists
        table_exists = conn.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables 
                WHERE table_name = 'silver_story_points'
            );
        """)).scalar()

        # Get array and non-array column names from story_point
        fetch_columns = lambda t: text(f"""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'story_point' AND data_type {t};
        """)
        array_cols = [r[0] for r in conn.execute(fetch_columns("LIKE 'ARRAY%'"))]
        non_array_cols = [r[0] for r in conn.execute(fetch_columns("NOT LIKE 'ARRAY%'"))]

        if not array_cols:
            print("⚠ No array columns found in story_point.")
            return

        # Generate dynamic array expansions like relates_to_1, relates_to_2, ...
        extracted_cols = [
            f"({col}[{i}])::TEXT AS {col}_{i}"
            for col in array_cols
            for i in range(1, (conn.execute(text(f"SELECT MAX(array_length({col}, 1)) FROM story_point")).scalar() or 1) + 1)
        ]
        select_query = f"""
            SELECT {", ".join(non_array_cols + extracted_cols)}
            FROM story_point
        """

        # Create the table if it does not exist
        if not table_exists:
            conn.execute(text(f"""
                CREATE TABLE silver_story_points AS
                {select_query};
            """))
            # Add unique constraint
            conn.execute(text("""
                ALTER TABLE silver_story_points
                ADD CONSTRAINT silver_story_points_unique UNIQUE (issue_id, emp_id, task, start_date);
            """))
            print("✅ Created 'silver_story_points' table with expanded data.")
        else:
            # Ensure unique constraint exists
            conn.execute(text(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.table_constraints
                        WHERE table_name = 'silver_story_points' 
                        AND constraint_type = 'UNIQUE'
                        AND constraint_name = 'silver_story_points_unique'
                    ) THEN
                        ALTER TABLE silver_story_points
                        ADD CONSTRAINT silver_story_points_unique UNIQUE (issue_id, emp_id, task, start_date);
                    END IF;
                END
                $$;
            """))

            # Perform UPSERT
            col_names = non_array_cols + [col.split(" AS ")[1] for col in extracted_cols]
            update_set = ", ".join([
                f"{col} = EXCLUDED.{col}"
                for col in col_names
                if col not in ['issue_id', 'emp_id', 'task', 'start_date']
            ])

            insert_query = f"""
                INSERT INTO silver_story_points ({", ".join(col_names)})
                {select_query}
                ON CONFLICT (issue_id, emp_id, task, start_date)
                DO UPDATE SET {update_set};
            """

            conn.execute(text(insert_query))
            print("✅ UPSERTED records into 'silver_story_points' table.")

        conn.commit()

#############################################################
# ----- Master Task: Preprocess All Tables -----
@celery_app.task(name="tasks.preprocess_tables")
def preprocess_tables():
    logger.info("🔹 Starting master preprocessing task")
    results = {
        'leave': preprocess_leave(),
        'employee': preprocess_employee(),
        'attendance': preprocess_attendance(),
        'organization': preprocess_organization(),
        'story_point': preprocess_story_point()
    }
    logger.info("✅ All preprocessing tasks completed.")
    return results

# ----- Master Task: Ingest then Preprocess -----
@celery_app.task(name="tasks.ingest_and_preprocess")
def ingest_and_preprocess():
    ingestion = ingest_data()
    preprocessing = preprocess_tables()
    return {"ingestion": ingestion, "preprocessing": preprocessing}

# ----- Celery Beat Scheduling -----
celery_app.conf.beat_schedule.update({
    'ingest-and-preprocess-every-2-minutes': {
        'task': 'tasks.ingest_and_preprocess',
        'schedule': crontab(minute='*/1'),
    },
})
"""
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Unrestricted
.venv\Scripts\activate
docker desktop start
1. celery -A celery_app beat --loglevel=info
2. celery -A celery_app worker --loglevel=info --pool=solo
3. streamlit run app.py
//REDIS
1. docker exec -it redis_container redis-cli ping 
(if not running) docker start redis_container
2. docker run -d --name redis_container -p 6379:6379 redis
// TRINO
docker restart trino_container
1. docker exec -it trino_container /bin/bash
2. trino
    3. SELECT * FROM employee_burnout_view LIMIT 10;
"""
