import trino
import pandas as pd
import streamlit as st
import plotly.express as px
import matplotlib.cm as cm
import matplotlib.colors as mcolors
from sqlalchemy import create_engine, text
####################################
def local_css(file_name):
    with open(file_name) as f:
        st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)
local_css("style.css") 
####################################
def get_trino_connection():
    return trino.dbapi.connect(
        host="host.docker.internal",
        port=8080,
        user="admin",
        catalog="postgresql",  
        schema="public")

####################################
def fetch_data(query):
    try:
        conn = get_trino_connection()
        cur = conn.cursor()
        cur.execute(query)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]
        return pd.DataFrame(rows, columns=columns)
    except Exception as e:
        st.error(f"Error fetching data: {e}")
        return pd.DataFrame()

############################################################
df = fetch_data("SELECT * FROM postgresql.public.task_insights_view")

# Convert dates
df["task_ended"] = pd.to_datetime(df["task_ended"])
df["due_date"] = pd.to_datetime(df["due_date"])

# Add month & year
df["month"] = df["task_ended"].dt.strftime('%B')
df["year"] = df["task_ended"].dt.year

# Get latest record info
latest_date = df["task_ended"].max()
latest_month = latest_date.strftime('%B')
latest_year = latest_date.year
latest_sprint = df[df["task_ended"] == latest_date]["cycle"].iloc[0] if not df[df["task_ended"] == latest_date].empty else None

# ---------------- Sidebar Filters ----------------
st.sidebar.markdown("#### Filter Tasks")

all_sprints = df["cycle"].dropna().unique()
all_months = df["month"].dropna().unique()

selected_sprints = st.sidebar.multiselect(
    "Select Sprint(s)", 
    options=sorted(all_sprints),
    default=[latest_sprint] if latest_sprint else [])

selected_months = st.sidebar.multiselect(
    "Select Month(s)", 
    options=sorted(all_months),
    default=[latest_month])

# Filter
filtered_df = df[
    df["cycle"].isin(selected_sprints) &
    df["month"].isin(selected_months)]

if filtered_df.empty:
    st.warning("⚠️ No data found for the selected Sprint and Month.")
    st.stop()
# ---------------- Charts ----------------
# Delayed task analysis
col1, col2 = st.columns(2)
with col1:
    delayed_df = filtered_df[
        filtered_df["overdue_status"].isin(["Late", "Overdue", "Severely Overdue"])
    ].groupby(["label", "overdue_status"]).size().reset_index(name="count")

    fig1 = px.bar(
        delayed_df,
        x="label",
        y="count",
        color="overdue_status",
        color_discrete_map={"Late": "pink", "Overdue": "orange", "Severely Overdue": "red"},
        title="Missed Deadlines & Delays Severity (Grouped by Team)",
        labels={"label": "Team", "count": "Number of Delayed Tasks"},
        template="plotly_white",
        barmode="group")
    st.plotly_chart(fig1, use_container_width=True)

# ------------------------------------
with col2:
    delay_counts = filtered_df.groupby(["label", "overdue_status"]).size().reset_index(name="count")
    fig2 = px.bar(
        delay_counts,
        x="label",
        y="count",
        color="overdue_status",
        title="Breakdown of Tasks by Team",
        labels={"count": "Number of Tasks", "label": "Team"},
        color_discrete_map={
            "On Time": "green", 
            "Late": "pink", 
            "Overdue": "orange", 
            "Severely Overdue": "red"
        },
        template="plotly_white",
        barmode="stack"
    )
    st.plotly_chart(fig2, use_container_width=True)

# ------------------------------------   
options = {
    "state": "State",
    "priority": "Priority",
    "label": "Label",
    "estimate": "Estimate",
    "overdue_status": "Task Status"}

#filters (cycle, overdue_days)
filter = {
    "None": "No Filter",
    "cycle": "Cycle",
    "overdue_days": "Overdue Days"}

st.sidebar.markdown("#### Filter Distribution")
group_by_col = st.sidebar.selectbox(
    "Group by:",
    filter.keys(),  
    format_func=lambda x: filter[x]  )

for key, value in options.items():
    if group_by_col != "None":
        df_count = df.groupby([key, group_by_col]).size().reset_index(name="issue_count")

        unique_categories = df_count[group_by_col].unique()
        viridis = cm.get_cmap('viridis', len(unique_categories)) 
        color_map = {
            category: mcolors.rgb2hex(viridis(i / len(unique_categories)))
            for i, category in enumerate(unique_categories)}

        fig = px.bar(
            df_count,
            x=key,
            y="issue_count",
            labels={"issue_count": "Issue Count", key: value},
            template="plotly_white",
            title=f"{value} Distribution (Grouped by: {filter[group_by_col]})",
            color=group_by_col, 
            color_discrete_map=color_map,  
            barmode="stack" )

    else:
        df_count = df.groupby(key).size().reset_index(name="issue_count")
        unique_categories = df_count[key].unique()
        viridis = cm.get_cmap('viridis', len(unique_categories))
        color_map = {
            category: mcolors.rgb2hex(viridis(i / len(unique_categories)))
            for i, category in enumerate(unique_categories)}

        fig = px.bar(
            df_count,
            x=key,
            y="issue_count",
            labels={"issue_count": "Issue Count", key: value},
            template="plotly_white",
            title=f"{value} Distribution", 
            color=key,  
            color_discrete_map=color_map,  
            barmode="stack" )
        
    fig.update_traces(width=0.5)
    st.plotly_chart(fig, use_container_width=True)

# ---------------- Sidebar CSV Upload ----------------
import ast

def get_sqlalchemy_engine():
    return create_engine(
        "postgresql+psycopg2://admin:password@209.38.56.184:5432/orion"
    )

# Function to safely parse array-like columns
def parse_array(val):
    if pd.isna(val) or val in ["", "NaN", "nan", None]:
        return None
    try:
        return ast.literal_eval(val) if isinstance(val, str) else [val]
    except:
        return [val]

# ---------------- Sidebar CSV Upload ----------------
st.sidebar.markdown("---")
st.sidebar.markdown("#### ⬆️ Import CSV to PostgreSQL")

uploaded_file = st.sidebar.file_uploader("Choose a CSV file", type="csv")
if uploaded_file is not None:
    table_name = st.sidebar.text_input("Target Table Name", value="story_point")

    if st.sidebar.button("Upload to DB"):
        try:
            # read CSV
            csv_data = pd.read_csv(uploaded_file)
            if csv_data.empty:
                st.sidebar.warning("Uploaded CSV file is empty")
                st.stop()

            # connect to DB
            engine = get_sqlalchemy_engine()

            # create table if not exists
            create_table_query = text(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id SERIAL PRIMARY KEY,
                    issue_id VARCHAR(50),
                    emp_id INT,
                    task TEXT,
                    state VARCHAR(20),
                    priority VARCHAR(20),
                    start_date DATE,
                    due_date DATE,
                    estimate INT,
                    labels VARCHAR(50),
                    task_ended DATE,
                    relates_to TEXT[],
                    sub_issue TEXT[],
                    blocked_by TEXT[],
                    module TEXT,
                    cycle VARCHAR(50),
                    UNIQUE(issue_id, emp_id, task, start_date)
                );
            """)

            with engine.connect() as conn:
                conn.execute(create_table_query)
                conn.commit()

            # UPSERT into DB
            with engine.begin() as conn:
                for _, row in csv_data.iterrows():
                    upsert_query = text(f"""
                        INSERT INTO {table_name} (
                            issue_id, emp_id, task, state, priority, start_date, due_date,
                            estimate, labels, task_ended, relates_to, sub_issue, blocked_by,
                            module, cycle
                        )
                        VALUES (
                            :issue_id, :emp_id, :task, :state, :priority, :start_date, :due_date,
                            :estimate, :labels, :task_ended, :relates_to, :sub_issue, :blocked_by,
                            :module, :cycle
                        )
                        ON CONFLICT (issue_id, emp_id, task, start_date)
                        DO UPDATE SET
                            state = EXCLUDED.state,
                            priority = EXCLUDED.priority,
                            due_date = EXCLUDED.due_date,
                            estimate = EXCLUDED.estimate,
                            labels = EXCLUDED.labels,
                            task_ended = EXCLUDED.task_ended,
                            relates_to = EXCLUDED.relates_to,
                            sub_issue = EXCLUDED.sub_issue,
                            blocked_by = EXCLUDED.blocked_by,
                            module = EXCLUDED.module,
                            cycle = EXCLUDED.cycle;
                    """)

                    conn.execute(upsert_query, {
                        "issue_id": row["issue_id"],
                        "emp_id": int(row["emp_id"]) if not pd.isna(row["emp_id"]) else None,
                        "task": row["task"],
                        "state": row["state"],
                        "priority": row["priority"],
                        "start_date": row["start_date"],
                        "due_date": row["due_date"],
                        "estimate": int(row["estimate"]) if not pd.isna(row["estimate"]) else None,
                        "labels": row["labels"],
                        "task_ended": row["task_ended"],
                        "relates_to": parse_array(row.get("relates_to")),
                        "sub_issue": parse_array(row.get("sub_issue")),
                        "blocked_by": parse_array(row.get("blocked_by")),
                        "module": row["module"],
                        "cycle": row["cycle"]
                    })

            st.sidebar.success(f"✅ Successfully inserted/updated {len(csv_data)} rows to database")

        except Exception as e:
            if "duplicate key value violates unique constraint" in str(e):
                st.sidebar.error("❌ Data already exists")
            else:
                st.sidebar.error(f"❌ Error: {str(e)}")