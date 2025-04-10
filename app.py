import trino
import pandas as pd
import streamlit as st
import plotly.express as px
import joblib
import numpy as np

# Function to establish connection with Trino
def get_trino_connection():
    return trino.dbapi.connect(
        host="localhost",
        port=8080,
        user="admin",
        catalog="postgresql",  # Trino catalog
        schema="public"        # Change schema if needed
    )

# Fetch Data Function
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


# Sidebar for filters
st.sidebar.header("Filters")

# Fetch initial data for both views
df_leaderboard = fetch_data("SELECT * FROM postgresql.public.task_leaderboard")
df_burnout = fetch_data("SELECT * FROM postgresql.public.employee_burnout_view")

# Filter options for both views
sprint_filter = st.sidebar.selectbox("Select Sprint", df_leaderboard["cycle"].unique())
month_filter = st.sidebar.selectbox("Select Month", pd.to_datetime(df_leaderboard["sprint_month"]).dt.strftime("%B %Y").unique())

# Apply filter to both views
filtered_leaderboard_df = df_leaderboard[(df_leaderboard["cycle"] == sprint_filter) & 
                                          (pd.to_datetime(df_leaderboard["sprint_month"]).dt.strftime("%B %Y") == month_filter)]

filtered_burnout_df = df_burnout[(df_burnout["cycle"] == sprint_filter) & 
                                  (pd.to_datetime(df_burnout["month"]).dt.strftime("%B %Y") == month_filter)]

# ------------------------ Visualization 1: Leaderboard -------------------------
if not filtered_leaderboard_df.empty:

    filtered_leaderboard_df["employee_name"] = filtered_leaderboard_df["first_name"] + " " + filtered_leaderboard_df["last_name"]
    filtered_leaderboard_df["late_tasks"] = filtered_leaderboard_df["total_tasks"] - filtered_leaderboard_df["on_time_tasks"]
    
    melted_df = pd.melt(
        filtered_leaderboard_df,
        id_vars=["employee_name", "rank", "total_overdue_days", "total_priority_points"],
        value_vars=["on_time_tasks", "late_tasks"],
        var_name="task_status",
        value_name="task_count"
    )
    melted_df["task_status"] = melted_df["task_status"].map({
        "on_time_tasks": "On Time",
        "late_tasks": "Late"
    })

    # Create the stacked bar chart
    fig = px.bar(
        melted_df,
        x="task_count",
        y="employee_name",
        color="task_status",
        orientation="h",
        title="🏆 Sprint Leaderboard",
        labels={"task_count": "Number of Tasks", "employee_name": "Employee", "task_status": "Status"},
        hover_data={"total_overdue_days": True, "total_priority_points": True}
    )
    fig.update_layout(
        barmode="stack",
        yaxis=dict(autorange="reversed"),
        xaxis_title="Total Tasks",
        yaxis_title=""
    )
    # Add annotations for each employee's rank
    rank_labels = {1: "🏅 Top 1", 2: "🥈 Top 2", 3: "🥉 Top 3"}  # Customize labels if needed
    for _, row in filtered_leaderboard_df.iterrows():
        fig.add_annotation(
            x=row["total_tasks"] + 0.5,  # Offset the position slightly to the right of the bar
            y=row["employee_name"],
            text=rank_labels.get(row["rank"], f"🔹 Top {row['rank']}"),
            showarrow=False,
            font=dict(size=15),
            align="left"
        )

    st.plotly_chart(fig, use_container_width=True)

# ------------------------ Visualization 2: Burnout Risk ------------------------
# 🎯 Load the trained model for Burnout Prediction
MODEL_PATH = r"C:\Users\Sarah\Desktop\try1\model\random_forest_model.pkl"
model = joblib.load(MODEL_PATH)

# 🧠 Predict burnout risk
def predict_burnout(df):
    if df.empty:
        return df

    try:
        feature_columns = [
            "days", "total_tasks", "assigned_tasks", "completed_tasks", "task_backlog",
            "task_completion_rate_assigned", "task_completion_rate_total",
            "completion_time_days", "avg_task_completion_time", "minimum_working_hours",
            "overtime_frequency", "total_work_hours", "num_high_priority_tasks",
            "num_leaves_taken", "total_leave_days", "total_leave_credits"
        ]
        
        features = df[feature_columns]

        df["burnout_risk"] = model.predict_proba(features)[:, 1] * 100  # Convert to percentage
        
        df["status"] = df["burnout_risk"].apply(
            lambda x: "🔴 High Risk" if x > 80 else ("🟠 Moderate Risk" if x > 65 else "🟢 Low Risk")
        )

        df["employee_name"] = df["first_name"] + " " + df["last_name"]

        # Convert float columns to integers 
        int_columns = [
            "total_tasks", "assigned_tasks", "completed_tasks", "task_backlog",
            "task_completion_rate_assigned", "task_completion_rate_total",
            "completion_time_days", "avg_task_completion_time",
            "total_work_hours", "overtime_frequency",
            "num_high_priority_tasks", "num_leaves_taken", "total_leave_days", "total_leave_credits"
        ]

        # Handle NaN & Inf before converting
        df[int_columns] = df[int_columns].replace([np.inf, -np.inf], 0).fillna(0).astype(int)
        # 🔹 Format completion rate as percentages (e.g., "85%")
        df["task_completion_rate_assigned"] = df["task_completion_rate_assigned"].replace([np.inf, -np.inf], 0).fillna(0).astype(int).astype(str) + "%"
        df["task_completion_rate_total"] = df["task_completion_rate_total"].replace([np.inf, -np.inf], 0).fillna(0).astype(int).astype(str) + "%"

        return df
    except Exception as e:
        st.error(f"Prediction Error: {e}")
        return df

if not filtered_burnout_df.empty:
    st.markdown("#### 🔍 Employee Burnout Risk Table")
    predictions = predict_burnout(filtered_burnout_df)

    display_df = predictions.rename(columns={
        "employee_name": "Employee Name",
        "total_tasks": "Total Tasks",
        "completed_tasks": "Completed Tasks",
        "task_backlog": "Task Backlog",
        "task_completion_rate_assigned": "Task Completion Rate (Assigned) %",
        "task_completion_rate_total": "Task Completion Rate (Total) %",
        "total_work_hours": "Total Work Hours",
        "overtime_frequency": "Overtime Frequency",
        "num_high_priority_tasks": "High-Priority Tasks",
        "num_leaves_taken": "Leaves Taken",
        "total_leave_days": "Total Leave Days",
        "total_leave_credits": "Total Leave Credits",
        "burnout_risk": "Burnout Risk (%)",
        "status": "Burnout Status"
    })

    # Display the table with color coding for burnout risk
    st.dataframe(
        display_df[[
            "Employee Name", "Total Tasks", "Completed Tasks", "Task Backlog",
            "Task Completion Rate (Assigned) %", "Task Completion Rate (Total) %",
            "Total Work Hours", "Overtime Frequency", "High-Priority Tasks", "Leaves Taken",
            "Total Leave Days", "Total Leave Credits", "Burnout Risk (%)", "Burnout Status"
        ]].style.applymap(lambda val: "background-color: red; color: white" if val > 80
                         else ("background-color: orange; color: white" if val > 65 else "background-color: green; color: white"),
                         subset=["Burnout Risk (%)"])
    )
else:
    st.warning("No data available for Burnout Risk.")
