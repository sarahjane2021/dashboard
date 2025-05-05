import trino
import pandas as pd
import streamlit as st
import plotly.express as px
import joblib
import numpy as np
import os

#---------- connection with Trino------
def get_trino_connection():
    return trino.dbapi.connect(
        host="host.docker.internal",
        port=8080,
        user="admin",
        catalog="postgresql",  
        schema="public"       )

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

# --------------------------------------------
st.sidebar.header("Filters")

# Fetch data 
df_leaderboard = fetch_data("SELECT * FROM postgresql.public.task_leaderboard")
df_burnout = fetch_data("SELECT * FROM postgresql.public.burnout_view")

# Filter options for both views
sprint_filter = st.sidebar.selectbox("Select Sprint", df_leaderboard["cycle"].unique())
month_filter = st.sidebar.selectbox("Select Month", pd.to_datetime(df_leaderboard["sprint_month"]).dt.strftime("%B %Y").unique())

# filter to both views
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

    #stacked bar chart
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
        yaxis_title="",
        legend=dict(
            orientation="h", 
            yanchor="bottom",
            y=-0.3, 
            xanchor="center",
            x=0.5,
            title_text="" 
        )
    )
    # annotations for each employee's rank with adjusted positioning
    rank_labels = {1: "🏅 Top 1", 2: "🥈 Top 2", 3: "🥉 Top 3"}
    for _, row in filtered_leaderboard_df.iterrows():
        fig.add_annotation(
            x=row["total_tasks"] + max(1, row["total_tasks"] * 0.10),  
            y=row["employee_name"],
            text=rank_labels.get(row["rank"], f"🔹 Top {row['rank']}"),
            showarrow=False,
            font=dict(size=14), 
            align="left",
            xanchor="left", 
        )

    st.plotly_chart(fig, use_container_width=True)

# ------------------------ Visualization 2: Burnout Risk ------------------------

# trained model for Burnout Prediction
MODEL_PATH  = os.path.join('model', 'rf_regression_model.pkl')
SCALER_PATH = os.path.join('model', 'scaler.pkl')

model = joblib.load(MODEL_PATH)
scaler = joblib.load(SCALER_PATH)

# Predict burnout risk
def predict_burnout(df):
    if df.empty:
        return df

    try:
        columns_to_exclude = [
            'month', 'cycle', 'task_start_date',
            'task_ended', 'task_due_date']
        df_model = df.drop(columns=columns_to_exclude, errors='ignore')

        FEATURE_COLUMNS = [
            'days', 'assigned_tasks', 'completed_tasks', 'task_backlog',
            'task_completion_rate_total', 'minimum_working_hours',
            'overtime_frequency', 'overtime_hours', 'total_work_hours',
            'avg_working_hours', 'num_high_priority_tasks',
            'num_leaves_taken', 'total_leave_days', 'total_leave_credits'
        ]
        df_model = df_model[FEATURE_COLUMNS]
        scaled_features = scaler.transform(df_model)
        predictions = model.predict(scaled_features)
        df['Predicted Burnout Risk (%)'] = predictions.round().astype(int).clip(0, 100)

        df["employee_name"] = df["first_name"] + " " + df["last_name"]

        # ---- Data Cleaning & Formatting -----
        int_columns = [
            "days", "assigned_tasks", "completed_tasks", "task_backlog",
            "task_completion_rate_total", "completion_time_days", "minimum_working_hours",
            "overtime_frequency", "overtime_hours", "total_work_hours", "avg_working_hours",
            "num_high_priority_tasks", "num_leaves_taken", "total_leave_days", "total_leave_credits"
        ]
        df[int_columns] = df[int_columns].replace([np.inf, -np.inf], 0).fillna(0).astype(int)

        df["task_completion_rate_total"] = df["task_completion_rate_total"].astype(str) + "%"
        df["days"] = df["days"].astype(str) + " days"
        df["completion_time_days"] = df["completion_time_days"].astype(str) + " days"
        df["total_work_hours"] = df["total_work_hours"].astype(str) + " hours"
        df["overtime_hours"] = df["overtime_hours"].astype(str) + " hours"

        return df
    except Exception as e:
        st.error(f"Prediction Error: {e}")
        return df

if not filtered_burnout_df.empty:
    st.markdown("###### 🔍 Employee Burnout Risk Table")
    predictions = predict_burnout(filtered_burnout_df)

    display_df = predictions.rename(columns={
        'cycle': 'Cycle',
        'days': 'Sprint Duration',
        'assigned_tasks': 'Assigned Tasks',
        'completed_tasks': 'Completed Tasks',
        'task_backlog': 'Task Backlog',
        'task_completion_rate_total': 'Task Completion Rate',
        'minimum_working_hours': 'Min Working Hours',
        'overtime_frequency': 'Overtime Frequency',
        'overtime_hours': 'Overtime Hours',
        'total_work_hours': 'Total Work Hours',
        'avg_working_hours': 'Avg Working Hours',
        'num_high_priority_tasks': 'High Priority Tasks',
        'num_leaves_taken': 'Leaves Taken',
        'Predicted Burnout Risk (%)': 'Burnout Risk (%)',
        'employee_name': 'Employee Name',
        'task_start_date': 'Start Date',
        'task_ended': 'End Date',
        'Predicted Burnout Risk (%)': 'Burnout Risk (%)',
        'task_due_date': 'Due Date'
    })
    # 🔹 Format datetime columns to 'e.g. Mar 21, 2025'
    date_columns = ['Start Date', 'Due Date', 'End Date']  # or whatever your date fields are
    for col in date_columns:
        display_df[col] = pd.to_datetime(display_df[col], errors='coerce')
        display_df[col] = display_df[col].dt.strftime('%b %d, %Y')

    # Set 'Employee Name' as the index
    display_df = display_df.set_index("Employee Name")

    # Display the table with color coding for burnout risk
    st.dataframe(
        display_df[[
            'Cycle', 'Start Date', 'End Date', 'Due Date',
            'Assigned Tasks', 'Completed Tasks', 'Task Backlog','Task Completion Rate',
            'Total Work Hours','Overtime Hours','High Priority Tasks','Leaves Taken', 'Burnout Risk (%)'
        ]].style.applymap(lambda val: "background-color: red; color: white" if val > 80
                         else ("background-color: orange; color: white" if val > 65 else "background-color: green; color: white"),
                         subset=["Burnout Risk (%)"])
    )
