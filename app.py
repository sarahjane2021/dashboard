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
df_leaderboard = fetch_data("SELECT * FROM postgresql.public.task_leaderboard_view")
df_burnout = fetch_data("SELECT * FROM postgresql.public.burnout_view")

# Filter options for both views
sprint_filter = st.sidebar.selectbox("Select Sprint", df_leaderboard["cycle"].unique())

# filter to both views
filtered_leaderboard_df = df_leaderboard[df_leaderboard["cycle"] == sprint_filter]

filtered_burnout_df = df_burnout[df_burnout["cycle"] == sprint_filter]

# ------------------------ Visualization 1: Leaderboard -------------------------
if not filtered_leaderboard_df.empty:
    # Add full name
    filtered_leaderboard_df["employee_name"] = (
        filtered_leaderboard_df["first_name"] + " " + filtered_leaderboard_df["last_name"]
    )

    # Filter Top 3 only (includes all ties with ranks 1, 2, 3)
    top3_df = filtered_leaderboard_df[filtered_leaderboard_df["rank"] <= 3].copy()

    if not top3_df.empty:
        # Assign emoji label for each rank, even if multiple employees share the same rank
        def get_rank_label(row):
            if row["rank"] == 1:
                return f"🥇 {row['employee_name']}"
            elif row["rank"] == 2:
                return f"🥈 {row['employee_name']}"
            elif row["rank"] == 3:
                return f"🥉 {row['employee_name']}"
            else:
                return row["employee_name"]

        top3_df["rank_label"] = top3_df.apply(get_rank_label, axis=1)

        # Assign color based on emoji
        color_map = {}
        for label in top3_df["rank_label"]:
            if "🥇" in label:
                color_map[label] = "gold"
            elif "🥈" in label:
                color_map[label] = "silver"
            elif "🥉" in label:
                color_map[label] = "peru"

        # Create bar chart
        fig = px.bar(
            top3_df,
            x="employee_name",
            y="total_estimate_points",
            text="completed_estimate_points",
            color="rank_label",
            color_discrete_map=color_map,
            title="Sprint Leaderboard"
        )

        fig.update_traces(textposition="outside")
        fig.update_layout(
            xaxis_title="Employee",
            yaxis_title="Total Estimate Points",
            yaxis=dict(tick0=0),
            showlegend=False,
            bargap=0.4  
        )

        st.plotly_chart(fig, use_container_width=True)

# ------------------------ Visualization 2: Burnout Risk ------------------------

# trained model for Burnout Prediction
MODEL_PATH  = os.path.join('model', 'rf_regression_model.pkl')
SCALER_PATH = os.path.join('model', 'scaler.pkl')

model = joblib.load(MODEL_PATH)
scaler = joblib.load(SCALER_PATH)

# Predict burnout risk function
def predict_burnout(df):
    if df.empty:
        return df

    try:
        columns_to_exclude = [
            'month', 'cycle', 'task_start_date',
            'task_ended', 'task_due_date'
        ]
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
        df['Burnout Risk (%)'] = predictions.round().astype(int).clip(0, 100)

        df["Employee Name"] = df["first_name"] + " " + df["last_name"]

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

# Apply prediction and visualize if data exists
if not filtered_burnout_df.empty:
    predictions = predict_burnout(filtered_burnout_df)

    # Burnout Risk Bar Chart (First)
    burnout_bar_df = predictions.sort_values(by='Burnout Risk (%)', ascending=False)

    fig = px.bar(
        burnout_bar_df,
        y='Employee Name',
        x='Burnout Risk (%)',
        text='Burnout Risk (%)',
        title='Employee Burnout Risk (%)',
        labels={'Employee Name': 'Employee', 'Burnout Risk (%)': 'Burnout Risk (%)'},
        color='Burnout Risk (%)',
        color_continuous_scale='reds',
        range_color=[0, 100]
    )

    fig.update_layout(
        xaxis=dict(range=[0, 100]),
        yaxis=dict(autorange="reversed")  # Most burnout on top
    )
    fig.update_traces(textposition='inside', texttemplate='%{text}%')
    st.plotly_chart(fig, use_container_width=True)

#-------------------- Dataframe Display --------------------
    display_df = predictions.rename(columns={
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
        'task_due_date': 'Due Date',
        'task_start_date': 'Start Date',
        'task_ended': 'End Date'
    })

    # Format date columns
    for col in ['Start Date', 'Due Date', 'End Date']:
        if col in display_df.columns:
            display_df[col] = pd.to_datetime(display_df[col], errors='coerce').dt.strftime('%b %d, %Y')

    # Set Employee Name as index
    display_df = display_df.set_index("Employee Name")

    # Styled Table with Burnout Risk Color Coding
    st.markdown("###### Employee Burnout Risk Table")
    st.dataframe(
        display_df[[
            'Assigned Tasks', 'Completed Tasks', 'Task Backlog', 'Task Completion Rate',
            'Total Work Hours', 'Overtime Hours', 'High Priority Tasks', 'Leaves Taken',
            'Burnout Risk (%)'
        ]]
    )