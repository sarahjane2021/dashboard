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
df = fetch_data("SELECT * FROM postgresql.public.attendance_view")
df_org = fetch_data("SELECT * FROM postgresql.public.silver_organization")
############################################################
# get the latest date
latest_date = df["date_today"].max()  
latest_data = df[df["date_today"] == latest_date] 

# Attendance Status Summary
st.markdown("📌 Attendance Status")
total_employees = len(df["emp_id"].unique())  
total_org = len(df_org["org_id"].unique())
col1, col2, col3 = st.columns(3)

metric_cards = [
    ("📌 No. Organizations", total_org), 
    ("📌 No. of Employees", total_employees), 
    ("✅ At Work", latest_data["approved_attendance"].sum())
]
for col, (title, value) in zip([col1, col2, col3], metric_cards):
    col.markdown(f"""
        <div class='card'>
            <div class='metric-title'>{title}</div>
            <div class='metric-value'>{value}</div>
        </div>
    """, unsafe_allow_html=True)
#---------------------
query = """
SELECT 
    a.full_name, a.organization_name, a.date_today, a.time_in, a.time_out, 
    a.status, a.type,
    CASE WHEN l.leave_id IS NOT NULL THEN 'On Leave' ELSE 'At Work' END AS leave_status
FROM postgresql.public.attendance_view a
LEFT JOIN postgresql.public.silver_leave l 
    ON a.emp_id = l.emp_id AND a.date_today BETWEEN l.duration_start_date AND l.duration_end_date
ORDER BY a.date_today DESC
"""
df = fetch_data(query)

# Convert to datetime for filtering
df["date_today"] = pd.to_datetime(df["date_today"])

# Sidebar Date Filter
st.sidebar.markdown("#### Filter by Date")
default_date = df["date_today"].max() if not df["date_today"].isnull().all() else pd.Timestamp("today").date()
start_date = st.sidebar.date_input("Start Date", value=default_date)
end_date = st.sidebar.date_input("End Date", value=default_date)

# Apply filtering
filtered_df = df[(df["date_today"] >= pd.to_datetime(start_date)) & (df["date_today"] <= pd.to_datetime(end_date))]

# Format columns for display
filtered_df["date_today"] = filtered_df["date_today"].dt.strftime("%b %d, %Y")
filtered_df["time_in"] = filtered_df["time_in"].apply(lambda x: x.strftime("%I:%M %p") if pd.notnull(x) else "N/A")
filtered_df["time_out"] = filtered_df["time_out"].apply(lambda x: x.strftime("%I:%M %p") if pd.notnull(x) else "N/A")

# Rename columns
filtered_df = filtered_df.rename(columns={
    "full_name": "Name",
    "organization_name": "Organization",
    "date_today": "Date",
    "time_in": "Time In",
    "time_out": "Time Out",
    "status": "Status",
    "type": "Work Type",
    "leave_status": "Leave Status"
})

# Display filtered dataframe
st.markdown("📋 Attendance Records")
st.dataframe(
    filtered_df[["Name", "Organization", "Date", "Time In", "Time Out", "Status", "Work Type", "Leave Status"]],
    use_container_width=True
)
#--------------------
st.divider() 
#########################################################
# Fetch Leave Data
df = fetch_data("SELECT * FROM postgresql.public.leave_visualizations")

# Sidebar Filter (Time Period)
st.sidebar.markdown("#### Filter Leave (Time Period)")
leave_filter = st.sidebar.selectbox("Select Time Period", ["3 Months", "6 Months", "9 Months", "1 Year"])

# Get the current month and year
current_month = datetime.now().strftime("%Y-%m")

# Convert leave_month to datetime format
df["leave_month"] = pd.to_datetime(df["leave_month"], format="%Y-%m").dt.strftime("%Y-%m")

# Calculate the cutoff month based on the selected filter
if leave_filter == "3 Months":
    cutoff_month = (datetime.now() - pd.DateOffset(months=3)).strftime("%Y-%m")
elif leave_filter == "6 Months":
    cutoff_month = (datetime.now() - pd.DateOffset(months=6)).strftime("%Y-%m")
elif leave_filter == "9 Months":
    cutoff_month = (datetime.now() - pd.DateOffset(months=9)).strftime("%Y-%m")
else:  # 1 Year
    cutoff_month = (datetime.now() - pd.DateOffset(years=1)).strftime("%Y-%m")

# Filter the dataframe based on the cutoff_month
filtered_df = df[df["leave_month"] >= cutoff_month]

color_map = {
    "Maternity Leave": "#98FB98",       
    "Paternity Leave": "#fa61ab", 
    "Sick Leave": "#1282be",        
    "Vacation Leave": "#708090",  
    "Special Leave for Women": "#87CEEB"
}

# Visualizations
col1, col2 = st.columns(2)
with col1:
    fig2 = px.bar(
        filtered_df,
        x="leave_month",
        y="approved_leave_count",
        color="type",
        title="Approved Leave Types Per Month",
        labels={"leave_month": "Month", "approved_leave_count": "Approved Leave Count", "type": "Leave Type"},
        barmode="stack",
        color_discrete_map=color_map 
    ).update_layout(
        legend=dict(orientation="h", x=0.1, xanchor="center" ) 
    )
    st.plotly_chart(fig2, use_container_width=True, theme='streamlit')

# ----------- Most Common Leave Types (Bar Chart) ---------------
leave_type_summary = filtered_df.groupby("type", as_index=False)["leave_type_count"].sum()
with col2:
    fig_type = px.bar(
        leave_type_summary, 
        x="type", 
        y="leave_type_count", 
        title="Most Common Leave Types",
        color="type",
        labels={"type": "Leave Type", "leave_type_count": "Leave Count"},
        orientation='h',
        color_discrete_map=color_map 
    ).update_layout(
        legend=dict(orientation="h", x=0.1, xanchor="center", y=-0.2),
        xaxis=dict(showticklabels=False)
    )
    st.plotly_chart(fig_type, use_container_width=True)

# Leave Trends (Line Chart)
col1, col2 = st.columns(2)
with col1:
    if not filtered_df.empty:
        filtered_df["leave_month"] = pd.to_datetime(filtered_df["leave_month"])

    leave_trends = filtered_df.groupby("leave_month", as_index=False)[
        ["approved_leave_count", "rejected_leave_count", "pending_status_count"]
    ].sum()

    leave_trends["leave_month"] = leave_trends["leave_month"].dt.strftime("%b %Y")
    fig1 = px.line(
        leave_trends,
        x="leave_month",
        y=["approved_leave_count", "rejected_leave_count", "pending_status_count"],
        markers=True,
        title="Leave Trends (Line Chart)",
        labels={"leave_month": "Month", "value": "Leave Count", "variable": "Leave Type"}
    ).update_layout(
        legend=dict(orientation="h", x=0.5, xanchor="center", y=-0.2)
    )
    st.plotly_chart(fig1, use_container_width=False)

# Leave Approval vs. Rejection (Donut Chart)
with col2:
    fig_donut = px.pie(
        pd.DataFrame({
            "status": ["Approved", "Rejected"],
            "count": [filtered_df["approved_leave_count"].sum(), filtered_df["rejected_leave_count"].sum()]
        }),
        values="count",
        names="status",
        title="Leave Approval vs. Rejection Rate",
        hole=0.4
    ).update_traces(
        textinfo="percent+label",
        texttemplate="<span style='font-size:18px'><b>%{percent:.0%}</b></span><br><span style='font-size:14px'>%{label}</span>", 
        textfont=dict(color="black"), 
        hoverinfo="label+percent+value"  
    ).update_layout(
        legend=dict(orientation="h", x=0.5, xanchor="center", y=-0.2),  
        title=dict(x=0.2)  
    )
    st.plotly_chart(fig_donut, use_container_width=True)
#--------------------
st.divider() 
#########################################################
