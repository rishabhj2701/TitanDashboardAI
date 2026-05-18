#!/bin/bash
# TitanDashboardAI Local Deployment Startup Script

# Define the cleanup function that runs on exit
cleanup() {
    echo ""
    echo "======================================================"
    echo "    Stopping all services to save battery...          "
    echo "======================================================"
    echo ">>> Shutting down Docker containers (Database, API, Redis)..."
    docker compose --profile localdb down
    echo ">>> Shutdown complete! Battery is safe."
    exit 0
}

# Trap Ctrl+C (SIGINT) and call the cleanup function
trap cleanup SIGINT SIGTERM

echo "======================================================"
echo "    Starting Titan Dashboard AI (Local Deployment)    "
echo "======================================================"
echo ""

# 1. Start the Docker Services
echo ">>> Starting Database, Redis, Backend API, and Frontend UI..."
docker compose --profile localdb up -d --build --force-recreate
echo ""
echo "    ✅ ALL SERVICES RUNNING!"
echo "    Open your browser to: http://localhost:8080"
echo "    Public share link:  ./demo-on.sh   (stop: ./demo-off.sh)"
echo "    [IMPORTANT] Press Ctrl+C in this terminal at any time to stop ALL services and shut down."
echo "======================================================"
echo ""

# Tail logs to keep the script running so it can catch Ctrl+C
docker compose --profile localdb logs -f

