from sqlalchemy import create_engine, text

TARGET_DATABASE_URL = "postgresql://admin:password@209.38.56.184:5432/orion"

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

# Call the function
preprocess_story_point()
