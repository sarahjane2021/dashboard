from celery import Celery
import pandas as pd
from sqlalchemy import create_engine, MetaData, insert, select, inspect
from sqlalchemy.dialects.postgresql import insert
from celery.schedules import crontab
import redis
import requests
from sqlalchemy.schema import Column
from sqlalchemy.types import String, Integer, Float, Boolean, Date, DateTime
import logging
import os
import re

# 🔹 Setup Logging
LOG_FILE = "celery.log"
if not os.path.exists(LOG_FILE):
    open(LOG_FILE, "w").close()  # Ensure log file exists

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
logger.info("🔹 Celery logging initialized.")

# 🔹 Configure Celery
celery_app = Celery(
    'tasks',
    broker='redis://localhost:6379/0',
    backend='redis://localhost:6379/1'
)

# 🔹 Redis Connection
redis_client = redis.Redis(host="localhost", port=6379, db=2, decode_responses=True)

# 🔹 Source and Target Database Connections
SRC_DATABASE_URL = "postgresql://admin:password@209.38.56.184:5432/postgres"
TARGET_DATABASE_URL = "postgresql://admin:password@209.38.56.184:5432/orion"

src_engine = create_engine(SRC_DATABASE_URL)
target_engine = create_engine(TARGET_DATABASE_URL)

#########################################################
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

#########################################################
# -------------------------------
# Helper Functions for Incremental Update
# -------------------------------

def table_exists(engine, table_name):
    """Check if a table exists in the target database."""
    inspector = inspect(engine)
    return inspector.has_table(table_name)

def get_last_processed_key(silver_table, key_col):
    """
    Retrieve the maximum primary key value from the silver table.
    silver_table: name of the silver table.
    key_col: the column name to use as an incremental key.
    """
    try:
        query = f"SELECT MAX({key_col}) as max_key FROM {silver_table}"
        result = pd.read_sql_query(query, target_engine)
        return result['max_key'].iloc[0]
    except Exception as e:
        logger.error(f"Error fetching last processed key from {silver_table}: {e}")
        return None

# -------------------------------
# Individual Preprocessing Functions with Incremental Logic
# -------------------------------

def _preprocess_leave():
    try:
        logger.info("🔹 Starting preprocessing for leave table")
        silver_table = "silver_leave"
        pk_column = "leave_id"  # Change this if your leave table uses a different key, e.g., "leave_id"

        # Determine whether to process all rows or only new rows.
        if table_exists(target_engine, silver_table):
            last_key = get_last_processed_key(silver_table, pk_column)
            query = f"SELECT * FROM leave WHERE {pk_column} > {last_key}" if last_key is not None else "SELECT * FROM leave"
            write_option = 'append'
        else:
            query = "SELECT * FROM leave"
            write_option = 'replace'
            
        df = pd.read_sql_query(query, target_engine)
        if df.empty:
            logger.info("No new leave records to process.")
            return "No new leave records to process."
        
        # Process the leave table data
        df['duration_start'] = pd.to_datetime(df['duration_start'])
        df['duration_end'] = pd.to_datetime(df['duration_end'])
        df['duration_start_date'] = df['duration_start'].dt.date
        df['duration_start_time'] = df['duration_start'].dt.time
        df['duration_end_date'] = df['duration_end'].dt.date
        df['duration_end_time'] = df['duration_end'].dt.time
        df_clean = df.drop(columns=['duration_start', 'duration_end'])
        
        # Write to silver table
        df_clean.to_sql(silver_table, target_engine, index=False, if_exists=write_option)
        logger.info("✅ Silver leave table updated successfully.")
        return "Silver leave table updated successfully."
    
    except Exception as e:
        logger.error(f"❌ Preprocessing leave table failed: {str(e)}")
        return str(e)

def _preprocess_employee():
    try:
        logger.info("🔹 Starting preprocessing for employee table")
        silver_table = "silver_employee"
        pk_column = "emp_id"  # Adjust this if your employee table uses a different key
        
        if table_exists(target_engine, silver_table):
            last_key = get_last_processed_key(silver_table, pk_column)
            query = f"SELECT * FROM employee WHERE {pk_column} > {last_key}" if last_key is not None else "SELECT * FROM employee"
            write_option = 'append'
        else:
            query = "SELECT * FROM employee"
            write_option = 'replace'
            
        df = pd.read_sql_query(query, target_engine)
        if df.empty:
            logger.info("No new employee records to process.")
            return "No new employee records to process."
        
        df_clean = df.drop(columns=['fingerprint', 'face_recognition'], errors='ignore')
        df_clean.to_sql(silver_table, target_engine, index=False, if_exists=write_option)
        logger.info("✅ Silver employee table updated successfully.")
        return "Silver employee table updated successfully."
    
    except Exception as e:
        logger.error(f"❌ Preprocessing employee table failed: {str(e)}")
        return str(e)

def _preprocess_organization():
    try:
        logger.info("🔹 Starting preprocessing for organization table")
        silver_table = "silver_organization"
        pk_column = "org_id"  # Adjust this if your organization table uses a different key
        
        if table_exists(target_engine, silver_table):
            last_key = get_last_processed_key(silver_table, pk_column)
            query = f"SELECT * FROM organization WHERE {pk_column} > {last_key}" if last_key is not None else "SELECT * FROM organization"
            write_option = 'append'
        else:
            query = "SELECT * FROM organization"
            write_option = 'replace'
            
        df = pd.read_sql_query(query, target_engine)
        if df.empty:
            logger.info("No new organization records to process.")
            return "No new organization records to process."
        
        # Function to count working days from the working_days column
        def count_days(x):
            if isinstance(x, list):
                return len(x)
            elif isinstance(x, str):
                x = x.strip('{}')
                return len(x.split(',')) if x else 0
            return 0
        
        df['num_working_days'] = df['working_days'].apply(count_days)
        df.to_sql(silver_table, target_engine, index=False, if_exists=write_option)
        logger.info("✅ Silver organization table updated successfully.")
        return "Silver organization table updated successfully."
    
    except Exception as e:
        logger.error("❌ Preprocessing organization table failed: " + str(e))
        return str(e)

def _preprocess_attendance():
    try:
        logger.info("🔹 Starting preprocessing for attendance table")
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
            # Convert 'time_out' to a 12-hour time format (e.g., "05:00 PM")
            df_clean['time_out'] = pd.to_datetime(
                df_clean['time_out'].astype(str), format='%H:%M:%S'
            ).dt.strftime('%I:%M %p')
        
        df_clean.to_sql(silver_table, target_engine, index=False, if_exists=write_option)
        logger.info("✅ Silver attendance table updated successfully.")
        return "Silver attendance table updated successfully."
    
    except Exception as e:
        logger.error(f"❌ Preprocessing attendance table failed: {str(e)}")
        return str(e)

# -------------------------------
# Master Preprocessing Task
# -------------------------------

@celery_app.task(name="tasks.preprocess_tables")
def preprocess_tables():
    logger.info("🔹 Starting master preprocessing task")
    results = {}
    results['leave'] = _preprocess_leave()
    results['employee'] = _preprocess_employee()
    results['organization'] = _preprocess_organization()
    results['attendance'] = _preprocess_attendance()
    logger.info("✅ All preprocessing tasks completed.")
    return results

# -------------------------------
# Celery Beat Scheduling
# -------------------------------

celery_app.conf.beat_schedule = {
    'ingest-db-every-2-minutes': {
        'task': 'tasks.ingest_data',
        'schedule': crontab(minute='*/2'),
    },
    'preprocess-silver-tables-every-2-minutes': {
        'task': 'tasks.preprocess_tables',
        'schedule': crontab(minute='*/2'),
    },
}
celery_app.conf.timezone = 'UTC'

"""
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Unrestricted
.venv\Scripts\activate
docker desktop start
1. celery -A celery_app beat --loglevel=info
2. celery -A celery_app worker --loglevel=info --pool=solo
3. streamlit run app.py
"""
