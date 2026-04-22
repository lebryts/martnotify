import os
from api.index import app

if __name__ == '__main__':
    # Set dummy environment variables for local test
    os.environ['REDIS_URL'] = "" # Will use mock
    os.environ['CRON_SECRET'] = "test_secret"
    
    print("Starting local test server at http://localhost:5000")
    print("Static files served from: " + app.static_folder)
    app.run(port=5000, debug=True)
