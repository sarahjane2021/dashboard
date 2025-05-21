import trino
import pandas as pd
import streamlit as st
import plotly.express as px
from datetime import datetime
####################################
####################################
# load custom CSS
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
        schema="public"     )
####################################
# fetch data from Trino
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
df = fetch_data("SELECT * FROM postgresql.public.attendance_viewt")
df_org = fetch_data("SELECT * FROM postgresql.public.silver_organization")
############################################################

# Get the latest date
latest_date = df["date_today"].max()
latest_data = df[df["date_today"] == latest_date]

# Clean up capitalization if needed
df["status"] = df["status"].str.strip().str.title()

total_employees = int(df["emp_id"].nunique())
on_time = (latest_data["status"] == "On Time").sum()
late = (latest_data["status"] == "Late").sum()
absent = (latest_data["status"] == "Absent").sum()

# Count of employees on leave (if you track it separately)
on_leave = (latest_data["leave_status"] == "On Leave").sum() if "leave_status" in latest_data.columns else 0

# Layout: 5 columns for 5 metrics
col1, col2, col3, col4, col5 = st.columns(5)

metric_cards = [
    ("No. of Employees", total_employees),
    ("At Work (On-Time)", on_time),
    ("At Work (Late)", late),
    ("On Leave", on_leave),
    ("Absent", absent)
]

for col, (title, value) in zip([col1, col2, col3, col4, col5], metric_cards):
    col.markdown(f"""
        <div class='card'>
            <div class='metric-title'>{title}</div>
            <div class='metric-value'>{value}</div>
        </div>
    """, unsafe_allow_html=True)

#--------- DATAFRAME------------

query = """
SELECT 
    a.fullname, a.date_today, a.attendance_time_in, a.attendance_time_out, 
    a.status,
    CASE WHEN l.leave_id IS NOT NULL THEN 'On Leave' ELSE NULL END AS leave_status
FROM postgresql.public.attendance_viewt a
LEFT JOIN postgresql.public.silver_leave l 
    ON a.emp_id = l.emp_id AND a.date_today BETWEEN l.duration_start_date AND l.duration_end_date
ORDER BY a.date_today DESC
"""
df = fetch_data(query)

# Convert and clean dates
df["date_today"] = pd.to_datetime(df["date_today"], errors='coerce')
df = df[df["date_today"].notnull()]

# Sidebar date filter
st.sidebar.markdown("#### Filter by Date")
default_date = df["date_today"].max() if not df["date_today"].isnull().all() else pd.Timestamp("today").date()
start_date = st.sidebar.date_input("Start Date", value=default_date)
end_date = st.sidebar.date_input("End Date", value=default_date)

# Apply date filter
filtered_df = df[(df["date_today"] >= pd.to_datetime(start_date)) & (df["date_today"] <= pd.to_datetime(end_date))]

# Format and rename columns
filtered_df["Date"] = filtered_df["date_today"].dt.strftime("%b %d, %Y")
filtered_df["Time In"] = filtered_df["attendance_time_in"].apply(lambda x: pd.to_datetime(x).strftime("%I:%M %p") if pd.notnull(x) else "N/A")
filtered_df["Time Out"] = filtered_df["attendance_time_out"].apply(lambda x: pd.to_datetime(x).strftime("%I:%M %p") if pd.notnull(x) else "N/A")

# Final formatting
filtered_df = filtered_df.rename(columns={
    "fullname": "Employee Name",
    "status": "Status"
})

# Optional: Combine status and leave info
filtered_df["Status"] = filtered_df.apply(lambda row: "On Leave" if pd.notnull(row["leave_status"]) else row["Status"], axis=1)
# Set Employee Name as index
filtered_df = filtered_df.set_index("Employee Name")

# Display
st.markdown("##### Attendance Records")
st.dataframe(
    filtered_df[["Date", "Time In", "Time Out", "Status"]],
    use_container_width=True
)
#--------------------
# Reset index to bring 'Employee Name' back as a column
filtered_df = filtered_df.reset_index()

# Group by Employee Name and Status
status_summary = filtered_df.groupby(["Employee Name", "Status"]).size().reset_index(name="count")

# Create bar chart
fig = px.bar(
    status_summary,
    x="Employee Name",
    y="count",
    color="Status",
    title="Stacked Bar Chart Per Employee - Attendance Status",
    labels={"Employee Name": "Employee", "count": "Number of Days"},
    color_discrete_map={
        "On-time": "green",
        "Late": "orange",
        "On Leave": "blue",
        "Absent": "red"
    },
    barmode="stack"
)

st.plotly_chart(fig, use_container_width=True)

st.divider()
#--------- Leave Data -----------
# Fetch Leave Data
df = fetch_data("SELECT * FROM postgresql.public.leave_visualizations")

# Get the current month and year
current_month = datetime.now().strftime("%Y-%m")

# Convert leave_month to datetime format
df["leave_month_dt"] = pd.to_datetime(df["leave_month"], format="%Y-%m", errors="coerce")
df["leave_month_label"] = df["leave_month_dt"].dt.strftime("%b %Y")
df = df.sort_values("leave_month_dt")

color_map = {
    "Maternity Leave": "#98FB98",       
    "Paternity Leave": "#fa61ab", 
    "Sick Leave": "#1282be",        
    "Vacation Leave": "#708090",  
    "Special Leave for Women": "#87CEEB"
}

# Prepare summary for second chart
leave_type_summary = df.groupby("type", as_index=False)["leave_type_count"].sum()
leave_type_summary = leave_type_summary.sort_values(by="leave_type_count", ascending=True)

# --- Column Layout ---
col1, col2 = st.columns(2)

# --- Left Column: Stacked Bar Chart ---
with col1:
    fig2 = px.bar(
        data_frame=df, 
        x="leave_month_label",
        y="approved_leave_count",
        color="type",
        title="Leave Types Per Month",
        labels={
            "leave_month_label": "Month",
            "approved_leave_count": "Approved Leave Count",
            "type": "Leave Type"
        },
        barmode="stack",
        color_discrete_map=color_map
    ).update_layout(
        legend=dict(orientation="h", x=0.1, xanchor="center", y=-0.2),
        xaxis=dict(showticklabels=True)
    )

    st.plotly_chart(fig2, use_container_width=True, theme='streamlit')

# --- Right Column: Horizontal Bar by Type ---
with col2:
    fig_type = px.bar(
        leave_type_summary, 
        x="type", 
        y="leave_type_count", 
        title="Leave Requests by Types",
        color="type",
        labels={"type": "Leave Type", "leave_type_count": "Leave Count"},
        color_discrete_map=color_map
    ).update_layout(
        legend=dict(orientation="h", x=0.1, xanchor="center", y=-0.2),
        xaxis=dict(showticklabels=True)
    )

    st.plotly_chart(fig_type, use_container_width=True)


# ----------------- Leave Trends (Line Chart)-->

df = fetch_data("SELECT * FROM postgresql.public.leave_visualizations")

# --- Convert to datetime ---
df["leave_month"] = pd.to_datetime(df["leave_month"], errors="coerce")
df["date"] = df["leave_month"]

# --- Place selector in a separate row ---
st.markdown("##### Leave Trend Analysis")
granularity = st.selectbox("Select Time Granularity", ["Daily", "Weekly", "Monthly"])

# --- Apply granularity ---
if granularity == "Daily":
    df["time_period"] = df["date"].dt.strftime("%Y-%m-%d")
elif granularity == "Weekly":
    df["time_period"] = df["date"].dt.to_period("W").apply(lambda r: r.start_time.strftime('%Y-%m-%d'))
elif granularity == "Monthly":
    df["time_period"] = df["date"].dt.strftime("%Y-%m")
st.divider()
# --- Group by period + type ---
grouped = df.groupby(["time_period", "type"], as_index=False)["leave_type_count"].sum()

# --- Plot the line trend ---
fig = px.line(
    grouped,
    x="time_period",
    y="leave_type_count",
    color="type",
    markers=True,
    title=f"Leave Requests Trend by Type ({granularity})",
    labels={
        "time_period": "Time Period",
        "leave_type_count": "Leave Count",
        "type": "Leave Type"
    }
).update_layout(
    xaxis_title="Time Period",
    yaxis_title="Leave Count",
    xaxis_tickangle=-45,
    legend=dict(orientation="h", x=0.5, xanchor="center", y=-0.3),
    margin=dict(t=50, b=80)
)

# --- Display the chart ---
st.plotly_chart(fig, use_container_width=True)
