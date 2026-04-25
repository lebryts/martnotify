import os
from api.index import app

if __name__ == '__main__':
    # Set dummy environment variables for local test
    os.environ['REDIS_URL'] = "MOCK" # Force immediate fallback if desired, or let it use default
    os.environ['CRON_SECRET'] = "test_secret"
    
    print("Starting local test server at http://localhost:5000")
    print("Static files served from: " + app.static_folder)
    app.run(port=5000, debug=True)
