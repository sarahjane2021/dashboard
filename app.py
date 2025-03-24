import trino
import pandas as pd
import streamlit as st
import psycopg2
from io import StringIO
import plotly.express as px
from streamlit_option_menu import option_menu
import json
####################################
st.set_page_config(layout='wide', page_title="Employee Insights Dashboard")
####################################
# Load custom CSS
def local_css(file_name):
    with open(file_name) as f:
        st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)

local_css("style.css") 
####################################
# Sidebar for navigation
####################################
with st.sidebar:
    selected = option_menu(
        'Project',
        ['Employee Performance Insights'],
        menu_icon='hospital-fill',
        icons=['activity'],
        default_index=0
    )

####################################

# Function to establish connection with Trino
def get_trino_connection():
    return trino.dbapi.connect(
        host="localhost",
        port=8080,
        user="admin",
        catalog="postgresql",  # Trino catalog
        schema="public"        # Change schema if needed
    )


# connection with PostgreSQL FOR INSERTING DATA
def get_postgres_connection():
    return psycopg2.connect(
        dbname="orion",
        user="admin",
        password="password",
        host="209.38.56.184",
        port="5432"
    )

# Function to fetch data from Trino
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
############################################################
st.markdown("#### Employee Attendance Dashboard")

# get the latest date
latest_date = df["date_today"].max()  
latest_data = df[df["date_today"] == latest_date] 

# Attendance Status Summary
st.markdown("📌 Attendance Status")
col1, col2, col3 = st.columns(3)

metric_cards = [
    ("✅ Approved", latest_data["approved_attendance"].sum()),
    ("❌ Rejected", latest_data["rejected_attendance"].sum()),
    ("🕒 Pending", latest_data["pending_attendance"].sum()),
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

# Format date and time columns
df["date_today"] = pd.to_datetime(df["date_today"]).dt.strftime("%b %d, %Y")
df["time_in"] = df["time_in"].apply(lambda x: x.strftime("%I:%M %p") if pd.notnull(x) else "N/A")
df["time_out"] = df["time_out"].apply(lambda x: x.strftime("%I:%M %p") if pd.notnull(x) else "N/A")

# Get most recent date
latest_date = df["date_today"].max()
latest_df = df[df["date_today"] == latest_date]

# Date inputs and filtering
default_date = pd.to_datetime(df["date_today"]).max() if not df["date_today"].isnull().all() else pd.Timestamp("today").date()
start_date, end_date = st.columns(2)
start_date = start_date.date_input("Start Date", value=default_date)
end_date = end_date.date_input("End Date", value=default_date)
filtered_df = df[(pd.to_datetime(df["date_today"]) >= pd.to_datetime(start_date)) & (pd.to_datetime(df["date_today"]) <= pd.to_datetime(end_date))]

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

st.dataframe(
    filtered_df[["Name", "Organization", "Date", "Time In", "Time Out", "Status", "Work Type", "Leave Status"]],
    use_container_width=True  
)
#--------------------
# Count metrics, # employees, on leave, at work
total_employees = latest_df.shape[0]
on_leave_count = latest_df[latest_df["leave_status"] == "On Leave"].shape[0]
not_on_leave_count = total_employees - on_leave_count 

col1, col2, col3 = st.columns(3)
with col1:
    st.divider() 
    col1.markdown(f"""
        <div class='card'>
            <div class='metric-title'>Total Employees</div>
            <div class='metric-value'>{total_employees}</div>
        </div>
        <div class='card'>
            <div class='metric-title'>On Leave</div>
            <div class='metric-value'>{on_leave_count}</div>
        </div>
        <div class='card'>
            <div class='metric-title'>At Work</div>
            <div class='metric-value'>{not_on_leave_count}</div>
        </div>
    """, unsafe_allow_html=True)

#########################################################
df = fetch_data("SELECT * FROM postgresql.public.leave_visualizations")
#########################################################
color_map = {
    "Maternity Leave": "#98FB98",       
    "Paternity Leave": "#fa61ab", 
    "Sick Leave": "#1282be",        
    "Vacation Leave": "#708090",  
    "Special Leave for Women": "#87CEEB"
}
#---------------------
## Approved Leave Types (Bar Chart)**
with col2:
    fig2 = px.bar(
        df,
        x="leave_month",
        y="approved_leave_count",
        color="type",
        title="Approved Leave Types Per Month",
        labels={"leave_month": "Month", "approved_leave_count": "Approved Leave Count", "type": "Leave Type"},
        barmode="stack",
        color_discrete_map=color_map 
    ).update_layout(
        legend=dict(orientation="h", x=0.1, xanchor="center", y=-0.2) 
    )

    st.plotly_chart(fig2, use_container_width=True, theme='streamlit')
#---------------------
## Most Common Leave Types (Bar Chart)**
leave_type_summary = df.groupby("type", as_index=False)["leave_type_count"].sum()
with col3:
    fig_type = px.bar(
        leave_type_summary, 
        x="type",  # Switch x and y
        y="leave_type_count",  # Switch x and y
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

#---------------------
col1, col2 = st.columns(2)

# Leave Trends (Line Chart)
with col1:
    if not df.empty:
        df["leave_month"] = pd.to_datetime(df["leave_month"])

    leave_trends = df.groupby("leave_month", as_index=False)[
        ["approved_leave_count", "rejected_count", "pending_status_count"]
    ].sum()

    leave_trends["leave_month"] = leave_trends["leave_month"].dt.strftime("%b %Y")
    fig1 = px.line(
        leave_trends,
        x="leave_month",
        y=["approved_leave_count", "rejected_count", "pending_status_count"],
        markers=True,
        title="Leave Trends (Line Chart)",
        labels={"leave_month": "Month", "value": "Leave Count", "variable": "Leave Type"}
    ).update_layout(
        legend=dict(orientation="h", x=0.5, xanchor="center", y=-0.2)
    )
    st.plotly_chart(fig1, use_container_width=False)
#---------------------
# Donut chart for Leave Approval vs. Rejection Rate
with col2:
    fig_donut = px.pie(
        pd.DataFrame({
            "status": ["Approved", "Rejected"],
            "count": [df["approved_count"].sum(), df["rejected_count"].sum()]
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
######################
df = fetch_data("SELECT * FROM postgresql.public.task_insights_view")
#######################

delayed_df = df[df["overdue_status"].isin(["Late", "Overdue", "Severely Overdue"])].groupby(["label", "overdue_status"]).size().reset_index(name="count")
fig = px.scatter(
    delayed_df, x="label", y="count", size="count", color="overdue_status",
    color_discrete_map={"Late": "pink", "Overdue": "orange", "Severely Overdue": "red"},
    title="Missed Deadlines & Delays Severity", labels={"label": "Team", "count": "Number of Delayed Tasks"},
    template="plotly_white"
)

st.plotly_chart(fig)
#--------------------------------
col1, col2 = st.columns(2)
with col1:
    st.divider() 
    delay_counts = df[df["overdue_status"] != "On Time"].groupby(["label", "overdue_status"]).size().reset_index(name="count")

    # Plot Stacked Bar Chart
    fig = px.bar(
        delay_counts,
        x="label",
        y="count",
        color="overdue_status",
        title="Breakdown of Overdue Tasks by Team",
        labels={"count": "Number of Delayed Tasks", "label": "Team"},
        color_discrete_map={"Late": "pink", "Overdue": "orange", "Severely Overdue": "red"},
        template="plotly_white",
        barmode="stack")

    st.plotly_chart(fig)
#--------------------------------
with col2:
    options = {
    "state": "State",
    "priority": "Priority",
    "label": "Label",
    "estimate": "Estimate",
    "overdue_status": "Task Status"
}


    st.markdown('<div class="custom-selectbox">', unsafe_allow_html=True)
    x_axis_option = st.selectbox("X-Axis:", options.keys())
    st.markdown("</div>", unsafe_allow_html=True)

    # count selected X-axis
    df_count = df[x_axis_option].value_counts().reset_index()
    df_count.columns = [x_axis_option, "issue_count"]

    fig = px.bar(
        df_count,
        x=x_axis_option,
        y="issue_count",
        labels={"issue_count": "Issue Count", x_axis_option: x_axis_option.capitalize()},
        template="plotly_white"
    )
    fig.update_traces(width=0.5) 
    st.plotly_chart(fig)

#######################

st.markdown("#### CSV Import to Story Points Table")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

if uploaded_file:
    # Read CSV with proper separator
    df = pd.read_csv(uploaded_file, sep=',')

    # Ensure JSON columns exist before applying transformations
    json_columns = ["relates_to", "sub_issue", "blocked_by"]
    for col in json_columns:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: json.loads(x.replace('""', '"')) if pd.notna(x) else None)
        else:
            df[col] = None  # Fill missing columns with None

    st.write("### Preview of Uploaded Data:")
    st.dataframe(df)

    if st.button("Import Data"):
        conn = get_trino_connection()
        cur = conn.cursor()

        for _, row in df.iterrows():
            query = f"""
                INSERT INTO story_points (
                    issue_id, emp_id, task, state, priority, start_date, due_date, estimate, labels, task_ended, relates_to, sub_issue, blocked_by, module, cycle
                ) VALUES (
                    '{row['issue_id']}', {row['emp_id']}, '{row['task']}', '{row['state']}', '{row['priority']}',
                    DATE '{row['start_date']}', DATE '{row['due_date']}', {row['estimate']}, '{row['labels']}',
                    {f"DATE '{row['task_ended']}'" if pd.notna(row['task_ended']) else 'NULL'},
                    {f"CAST('{json.dumps(row['relates_to'])}' AS JSON)" if row['relates_to'] else 'NULL'},
                    {f"CAST('{json.dumps(row['sub_issue'])}' AS JSON)" if row['sub_issue'] else 'NULL'},
                    {f"CAST('{json.dumps(row['blocked_by'])}' AS JSON)" if row['blocked_by'] else 'NULL'},
                    '{row['module']}', '{row['cycle']}'
                )
            """
            cur.execute(query)

        conn.commit()
        st.success("Data successfully imported into the database!")
