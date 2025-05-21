FROM python:3.12

# working directory
WORKDIR /app

# Copy everything from current directory to container
COPY . .

RUN pip install -r requirements.txt

# streamlit port
EXPOSE 8501

# run app
CMD ["streamlit", "run", "main.py", "--server.port=8501", "--server.address=0.0.0.0"]

# Build the Docker image #
# docker build -t my-streamlit-app .

# Run the Docker container #
# docker run -p 8501:8501 my-streamlit-app

#docker compose up --build
