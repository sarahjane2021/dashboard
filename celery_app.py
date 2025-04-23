from celery import Celery
import pandas as pd
from sqlalchemy import create_engine, MetaData, select, inspect, text, Table
from sqlalchemy.dialects.postgresql import insert
from celery.schedules import crontab
import redis, logging, os, re
from sqlalchemy.dialects.postgresql import insert  # for upsert logic


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
SRC_DATABASE_URL = "postgresql://admin:password@209.38.56.184:5432/postgres"
src_engine = create_engine(SRC_DATABASE_URL)
TARGET_DATABASE_URL = "postgresql://admin:password@209.38.56.184:5432/orion"
target_engine = create_engine(TARGET_DATABASE_URL)

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
# 🔹 Database Ingestion Task (Existing Logic)
@celery_app.task(name="tasks.ingest_data")
def ingest_data():
    try:
        logger.info("🔹 Starting database ingestion task")

        src_metadata = MetaData()
        src_metadata.reflect(bind=src_engine)

        target_metadata = MetaData()
        target_metadata.reflect(bind=target_engine)

        updated_tables = []  # Track tables with changes
        no_new_data = True   # Assume no new data unless proven otherwise

        for table_name, src_table in src_metadata.tables.items():
            if table_name not in target_metadata.tables:
                logger.info(f"🔹 Creating missing table `{table_name}` in target DB")
                src_table.metadata.create_all(target_engine)
                # Refresh target metadata to include the newly created table
                target_metadata.reflect(bind=target_engine)

            target_table = target_metadata.tables[table_name]

            # Step 1: Get the latest timestamp from the target DB
            if "updated_at" in [col.name for col in target_table.columns]:
                with target_engine.connect() as conn:
                    latest_timestamp = conn.execute(
                        select([target_table.c.updated_at]).order_by(target_table.c.updated_at.desc()).limit(1)
                    ).scalar()
            else:
                latest_timestamp = None  # If no updated_at column, fetch all rows

            # Step 2: Fetch only new/updated data from source
            query = f"SELECT * FROM {table_name}"
            if latest_timestamp:
                query += f" WHERE updated_at > '{latest_timestamp}'"  # Fetch only new/updated records

            new_data = pd.read_sql(query, src_engine)

            # Step 3: Skip processing if no new data
            if new_data.empty:
                continue  # Skip tables with no new data

            # Step 4: Check if target already has the exact same data
            with target_engine.connect() as conn:
                existing_data = pd.read_sql(f"SELECT * FROM {table_name}", conn)

            # Compare source and target
            if not existing_data.empty:
                merged_data = pd.concat([existing_data, new_data]).drop_duplicates(keep=False)
                if merged_data.empty:
                    continue  # No actual changes in data

            no_new_data = False  # Mark that at least one table has new data

            # Step 5: Convert PostgreSQL ARRAY format properly
            def parse_pg_array(value):
                if isinstance(value, str) and re.match(r"^{.*}$", value):
                    return value.strip("{}").split(",")
                return value

            column_types = {col.name: col.type for col in target_table.columns}
            for col_name, col_type in column_types.items():
                if "ARRAY" in str(col_type):
                    new_data[col_name] = new_data[col_name].apply(parse_pg_array)

            # Step 6: Perform an efficient UPSERT only for new/updated data
            primary_keys = [col.name for col in target_table.primary_key]
            update_columns = [col for col in new_data.columns if col not in primary_keys]

            stmt = insert(target_table).values(new_data.to_dict(orient="records"))
            stmt = stmt.on_conflict_do_update(
                index_elements=primary_keys,
                set_={col: stmt.excluded[col] for col in update_columns}
            )

            with target_engine.begin() as conn:
                result = conn.execute(stmt)
                if result.rowcount > 0:
                    updated_tables.append(table_name)
                    logger.info(f"✅ {result.rowcount} records updated in `{table_name}`")

        # Step 7: Log appropriate message
        if no_new_data:
            logger.info("✅ No new data added to all tables.")
            redis_client.set("ingestion_status", "✅ No new data added to all tables.")
        else:
            redis_client.set("ingestion_status", f"✅ Updated tables: {', '.join(updated_tables)}")

        logger.info("🔹 Database ingestion completed successfully.")
        return "Data ingestion completed successfully."

    except Exception as e:
        logger.error(f"❌ Database ingestion failed: {str(e)}")
        redis_client.set("ingestion_status", f"❌ Ingestion failed: {str(e)}")
        return str(e)
    
###########################################
# ----- Preprocessing Functions -----
from sqlalchemy import MetaData, Table
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.types import Time
import pandas as pd

def preprocess_leave():
    logger.info("🔹Preprocessing leave table")

    silver = "silver_leave"
    pk = "leave_id"

    # Step 1: Load source data
    df = pd.read_sql_query("SELECT * FROM leave", target_engine)
    if df.empty:
        logger.info("No new leave records to process.")
        return "No new leave records to process."

    # Step 2: Parse and split datetime columns
    df['duration_start'] = pd.to_datetime(df['duration_start'])
    df['duration_end'] = pd.to_datetime(df['duration_end'])

    df['duration_start_date'] = df['duration_start'].dt.date
    df['duration_start_time'] = df['duration_start'].dt.time
    df['duration_end_date'] = df['duration_end'].dt.date
    df['duration_end_time'] = df['duration_end'].dt.time

    df_clean = df.drop(columns=['duration_start', 'duration_end'])

    # Step 3: Reflect or create the silver table
    metadata = MetaData()
    metadata.reflect(bind=target_engine)
    if silver not in metadata.tables:
        logger.info("Silver table does not exist. Creating it.")
        df_clean.to_sql(silver, target_engine, index=False, if_exists='replace',
                        dtype={'duration_end_time': Time(), 'duration_start_time': Time()})
        return "Silver leave table created."

    silver_table = metadata.tables[silver]

    # Step 4: Perform upsert for each row
    with target_engine.begin() as conn:
        for _, row in df_clean.iterrows():
            row_dict = row.to_dict()

            insert_stmt = insert(silver_table).values(**row_dict)
            update_stmt = insert_stmt.on_conflict_do_update(
                index_elements=[pk],
                set_={col: insert_stmt.excluded[col] for col in row_dict if col != pk}
            )
            conn.execute(update_stmt)

    logger.info("Silver leave table upserted (inserted or updated).")
    return "Silver leave table upserted (inserted or updated)."

def preprocess_employee():
    logger.info("🔹Preprocessing employee table")
    silver = "silver_employee"; pk = "emp_id"
    if table_exists(target_engine, silver):
        last = get_last_processed_key(silver, pk)
        query = f"SELECT * FROM employee WHERE {pk} > {last}" if last is not None else "SELECT * FROM employee"
        option = 'append'
    else:
        query, option = "SELECT * FROM employee", 'replace'
    df = pd.read_sql_query(query, target_engine)
    if df.empty:
        logger.info("No new employee records to process.")
        return "No new employee records to process."
    df_clean = df.drop(columns=['fingerprint', 'face_recognition'], errors='ignore')
    df_clean.to_sql(silver, target_engine, index=False, if_exists=option)
    logger.info("Silver employee table updated.")
    return "Silver employee table updated."

def preprocess_organization():
    logger.info("🔹Preprocessing organization table")
    silver = "silver_organization"; pk = "org_id"
    if table_exists(target_engine, silver):
        last = get_last_processed_key(silver, pk)
        query = f"SELECT * FROM organization WHERE {pk} > {last}" if last is not None else "SELECT * FROM organization"
        option = 'append'
    else:
        query, option = "SELECT * FROM organization", 'replace'
    df = pd.read_sql_query(query, target_engine)
    if df.empty:
        logger.info("No new organization records to process.")
        return "No new organization records to process."
    def count_days(x):
        if isinstance(x, list):
            return len(x)
        elif isinstance(x, str):
            s = x.strip('{}')
            return len(s.split(',')) if s else 0
        return 0
    df['num_working_days'] = df['working_days'].apply(count_days)
    df.to_sql(silver, target_engine, index=False, if_exists=option)
    logger.info("Silver organization table updated.")
    return "Silver organization table updated."

from sqlalchemy.types import Time

def preprocess_attendance():
    try:
        logger.info("🔹 Preprocessing attendance table")
        silver_table = "silver_attendance"
        pk_column = "id"  # Adjust this if your attendance table uses a different key
        if table_exists(target_engine, silver_table):
            last_key = get_last_processed_key(silver_table, pk_column)
            query = f"SELECT * FROM attendance WHERE {pk_column} > {last_key}" if last_key is not None else "SELECT * FROM attendance"
            write_option = 'append'
        else:
            query = "SELECT * FROM attendance"
            write_option = 'replace'
        
        df = pd.read_sql_query(query, target_engine)
        if df.empty:
            logger.info("No new attendance records to process.")
            return "No new attendance records to process."

        df_clean = df.drop(columns=['photo'], errors='ignore')
        if 'time_out' in df_clean.columns:
            # Convert 'time_out' to a 12-hour formatted string, then convert back to a time object.
            time_str = pd.to_datetime(df_clean['time_out'].astype(str), format='%H:%M:%S').dt.strftime('%I:%M %p')
            df_clean['time_out'] = pd.to_datetime(time_str, format='%I:%M %p').dt.time
        
        # Write the cleaned DataFrame to the target database, ensuring 'time_out' is stored as TIME.
        df_clean.to_sql(silver_table, target_engine, index=False, if_exists=write_option,
                        dtype={'time_out': Time()})
        
        logger.info("✅ Silver attendance table updated successfully.")
        return "Silver attendance table updated successfully."
    
    except Exception as e:
        logger.error(f"❌ Preprocessing attendance table failed: {str(e)}")
        return str(e)


############################################################
############################################################

def preprocess_story_point():
    """Extracts array values into separate columns and updates 'silver_story_point' table."""
    engine = create_engine(TARGET_DATABASE_URL)
    
    with engine.connect() as conn:
        # Check if 'silver_story_point' exists
        table_exists = conn.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables 
                WHERE table_name = 'silver_story_point'
            );
        """)).scalar()

        # Get record counts
        total_records = conn.execute(text("SELECT COUNT(*) FROM story_point")).scalar()
        existing_records = conn.execute(text("SELECT COUNT(*) FROM silver_story_point"))\
            .scalar() if table_exists else 0

        if table_exists and existing_records == total_records:
            print("✅ No new story_point records to process.")
            return

        # Get column names
        fetch_columns = lambda t: text(f"""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'story_point' AND data_type {t};
        """)
        array_cols = [r[0] for r in conn.execute(fetch_columns("LIKE 'ARRAY%'"))]
        non_array_cols = [r[0] for r in conn.execute(fetch_columns("NOT LIKE 'ARRAY%'"))]

        if not array_cols:
            print("⚠ No array columns found in story_point.")
            return

        # Extract max array lengths and generate dynamic columns
        extracted_cols = [
            f"({col}[{i}])::TEXT AS {col}_{i}"
            for col in array_cols
            for i in range(1, (conn.execute(text(f"SELECT MAX(array_length({col}, 1)) FROM story_point")).scalar() or 1) + 1)
        ]

        # Create or replace the expanded table
        conn.execute(text(f"""
            DROP TABLE IF EXISTS silver_story_point;
            CREATE TABLE silver_story_point AS
            SELECT {", ".join(non_array_cols)}, {", ".join(extracted_cols)}
            FROM story_point;
        """))
        conn.commit()

        print(f"✅ {total_records - existing_records} new records added to story_point_expanded.")

#############################################################
# ----- Master Task: Preprocess All Tables -----
@celery_app.task(name="tasks.preprocess_tables")
def preprocess_tables():
    logger.info("🔹 Starting master preprocessing task")
    results = {
        'leave': preprocess_leave(),
        'employee': preprocess_employee(),
        'organization': preprocess_organization(),
        'attendance': preprocess_attendance(),
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

celery_app.conf.timezone = 'UTC'


"""
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Unrestricted
.venv\Scripts\activate
docker desktop start
1. celery -A celery_app beat --loglevel=info
2. celery -A celery_app worker --loglevel=info --pool=solo
3. streamlit run app.py
"""
