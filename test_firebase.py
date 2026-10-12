from firebase_db import db

try:
    # Test the connection by reading the documents collection.
    list(db.collection("documents").limit(1).stream())

    print("Firebase connected successfully!")
    print("Cloud Firestore is ready to use.")

except Exception as error:
    print("Firebase connection failed.")
    print(type(error).__name__, str(error))