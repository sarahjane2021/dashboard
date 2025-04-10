import trino
import pandas as pd
import streamlit as st
import plotly.express as px
import matplotlib.cm as cm
import matplotlib.colors as mcolors
####################################
st.set_page_config(layout='wide', page_title="Employee Insights Dashboard")
####################################
def local_css(file_name):
    with open(file_name) as f:
        st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)
local_css("style.css") 
####################################
def get_trino_connection():
    return trino.dbapi.connect(
        host="localhost",
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
            title=f"{value} Distribution (Grouped by {filter[group_by_col]})",
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
