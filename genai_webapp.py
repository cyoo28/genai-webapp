#!/usr/bin/env python3
# import packages
import os
import json
import boto3
from google import genai
from google.genai.types import GenerateContentConfig
from google.oauth2 import service_account
from flask import Flask, render_template, request, redirect, url_for, jsonify, session
import bcrypt
import pymysql
from threading import Lock
# define environment variables
os.environ["awsProfile"] = "ix-dev" # aws profile for boto3 session
os.environ["awsRegion"] = "us-east-1" # aws region for bot3 session
os.environ["gcpSecret"] = "gcp-genai-sa" # aws secret that contains gcp service account key
os.environ["gcpProject"] = "ix-sandbox" # gcp project where vertex ai resources are enabled
os.environ["gcpRegion"] = "us-east4" # gcp region where vertex ai resources are enabled
os.environ["geminiModel"] = "gemini-2.0-flash-001" # gemini model used for chatbot
os.environ["dbSecret"] = "mysql-db" # aws secret for MySQL db
os.environ["dbName"] = "genai" # name of MySQL database
os.environ["dbTable"] = "users" # table in MySQL database
os.environ["appParam"] = "webappKey" # aws parameter for webapp session key
os.environ["s3Bucket"] = "026090555438-genai-chat" # aws bucket for chat history
# define a MySql class
class MySQLClient:
    def __init__(self, host, user, password, db):
        # save MySQL database info
        self.host=host
        self.user=user
        self.password=password
        self.db=db
    def connect(self):
        # connect to the database
        conn = pymysql.connect(
            host=self.host,
            user=self.user,
            password=self.password,
            db=self.db,
            cursorclass=pymysql.cursors.DictCursor
        )
        return conn
    def disconnect(self, conn):
        # disconnect from the database
        conn.close()
    def add_entry(self, username, hashedPassword, email, table):
        # start a new connection
        conn = self.connect()
        # try to add a new user
        try:
            with conn.cursor() as cursor:
                sql = f"INSERT INTO {table} (username, password, email) VALUES (%s, %s, %s)"
                cursor.execute(sql, (username, hashedPassword, email))
            conn.commit()
            print(f"User '{username}' added successfully.")
        except Exception as e:
            print(f"[ERROR] Failed to add student: {e}")
        # close the connection
        finally:
            self.disconnect(conn)
    def read_table(self, table):
        # start a new connection
        conn = self.connect()
        # try to lookup all users
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT * FROM {table}")
                results = cursor.fetchall()
                return results
        except Exception as e:
            print(f"[ERROR] Failed to read users: {e}")
            return []
        # close the connection
        finally:
            self.disconnect(conn)
# define a s3 class
class MyS3Client:
    def __init__(self, session, bucket):
        # save the bucket name
        self.bucket = bucket
        # create an s3 client
        self.s3 = session.client("s3")
    def chatRead(self, chatKey):
        # retrieve the chat history
        response = self.s3.get_object(Bucket=self.bucket, Key=chatKey)
        chatJson = json.loads(response['Body'].read().decode('utf-8'))
        chatHistory = []
        for message in chatJson:
            parts = [{"text": part} for part in message["parts"]]
            chatHistory.append({
                "role": message["role"],
                "parts": parts
            })
        return chatHistory
    def chatWrite(self, chatKey, chatHistory):
        chatStr = []
        for message in chatHistory:
            parts = [part.text for part in message.parts]
            chatStr.append({
                "role": message.role,
                "parts": parts
            })
        chatJson = json.dumps(chatStr).encode("utf-8")
        # upload the chat history
        self.s3.put_object(Bucket=self.bucket, Key=chatKey, Body=chatJson, ContentType="application/json")
    def chatLookup(self, chatKey):
        # try to retrive head of chat log
        try:
            response = self.s3.head_object(Bucket=self.bucket, Key=chatKey)
            return response
        except self.s3.exceptions.ClientError as e:
            # if it doesn't exist return None
            if e.response['Error']['Code'] == '404':
                return None
            # otherwise, something else went wrong
            else:
                raise
# retrieve gcp service account key from aws secrets manager
awsSession = boto3.Session(profile_name=os.environ["awsProfile"], region_name=os.environ["awsRegion"])
smClient = awsSession.client("secretsmanager")
saResponse = smClient.get_secret_value(SecretId=os.environ["gcpSecret"])
saInfo = json.loads(saResponse["SecretString"])
# authenticate with gcloud service account
credentials = service_account.Credentials.from_service_account_info(
    saInfo,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
# set up genai client
genaiClient = genai.Client(vertexai=True, project=os.environ["gcpProject"], location=os.environ["gcpRegion"], credentials=credentials)
# model instructions
systemInstruction = """
  #You are a basic conversational assistant.
"""
# model configuration
config = GenerateContentConfig(
       system_instruction=systemInstruction,
       temperature=0.4, # [float, 0.0, 1.0] control randomness/creativity of response (0.0 is more deterministic, 1.0 is more liberal)
        top_p=0.95, # [float, 0.0, 1.0] consider min set of most probable tokens that exceed cumulative probability, p, and then randomly pick from the next token from this smaller set
        top_k=20, # [int, 1, 1000] consider the k most probable next tokens and then randomly pick from the next token from this smaller set
        candidate_count=1, # [int, 1, 8] control how many candidate responses the model should generate that the user can choose from
        max_output_tokens=1000, # [int, 1, inf] limit maximum tokens contained in response
        presence_penalty=0.0, # [float, -2.0, 2.0] negative values discourage use of new tokens while positive values encourage use of new tokens
        frequency_penalty=0.0, # [float, -2.0, 2.0] negative values encourage repetition of tokens while positive values discourage repetition of tokens
       )
# In-memory store for chatbots keyed by username
userChatbots = {}
chatbotsLock = Lock()
# create MySQLClient instance in aws
dbResponse = smClient.get_secret_value(SecretId=os.environ["dbSecret"])
dbInfo = json.loads(dbResponse["SecretString"])
sqlClient = MySQLClient("localhost", dbInfo["username"], dbInfo["password"], os.environ["dbName"])
# create MyS3Client instance
s3Client = MyS3Client(awsSession, os.environ["s3Bucket"])
# retrieve webapp session key from aws parameter store
ssmClient = awsSession.client("ssm")
appResponse = ssmClient.get_parameter(Name=os.environ["appParam"], WithDecryption=True)
appKey = appResponse["Parameter"]["Value"]
# set up flask webapp
app = Flask(__name__)
app.secret_key = appKey
# set route for landing page
@app.route("/")
def index():
    # check if the user has already signed in
    if "username" in session:
        # if they are, redirect to chat
        return redirect(url_for("chat"))
    # otherwise, redirect to login page
    else:
        return redirect(url_for("login"))
# set route for login page
@app.route("/login", methods=["GET", "POST"])
def login():
    # get users from rds
    users = sqlClient.read_table(os.environ["dbTable"])
    # allow users to input username and password
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        # check that the username/password is a valid login
        user = next((u for u in users if u['username'] == username), None)
        if user and bcrypt.checkpw(password.encode(), user["password"].encode()):
            # set session
            session["username"] = username
            # session expires if browser is closed
            session.permanent = False
            # redirect to the chat
            return redirect(url_for("select_chat"))
        else:
            # if not, stay on the login page and display error
            return render_template("login.html", error="Invalid username or password")
    # render the login page
    return render_template("login.html")
# set route for logout page
@app.route("/logout")
def logout():
    username = session.get("username")
    # delete chatbot
    if username:
        with chatbotsLock:
            userChatbots.pop(username, None)
    # clear session
    session.clear()
    # render the logout page (which redirects to the login)
    return render_template("logout.html")
# set route for the signup page
@app.route("/signup", methods=["GET", "POST"])
def signup():
    # get users from rds
    users = sqlClient.read_table(os.environ["dbTable"])
    # allow users to input username, password, and email
    if request.method == "POST":
        newUsername = request.form["username"]
        newPassword = request.form["password"]
        newEmail = request.form["email"]
        confirmEmail = request.form["confirmEmail"]
        # check for errors
        error = None
        # error if the username is already in use
        if any(newUsername == user["username"] for user in users):
            error = "Username already exists"
        # error if the email is already in use
        if any(newEmail == user["email"] for user in users):
            error = "Email already in use"
        # error if the email confirmation does not match
        elif newEmail != confirmEmail:
            error = "Emails do not match"
        # error if one of the fields is not filled out
        elif not newUsername or not newEmail or not newPassword:
            error = "Please enter a username, email, and password"
        # if there's an error
        if error:
            # stay on the signup page and display error
            return render_template("signup.html", error=error)
        # if no error,
        else:
            # generate salt to encrypt password
            salt = bcrypt.gensalt()
            # encrypt password
            hashedPassword = bcrypt.hashpw(newPassword.encode(), salt)
            # add new user to db
            sqlClient.add_entry(newUsername, hashedPassword, newEmail, os.environ["dbTable"])
            # redirect to login page
            return redirect(url_for("login"))
    # render signup page
    return render_template("signup.html")
# set route for forgot password page
@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    # get users from rds
    users = sqlClient.read_table(os.environ["dbTable"])
    # allow users to input email
    if request.method == "POST":
        email = request.form["email"]
        # check that the email exists in the userbase
        if any(email == user["email"] for user in users):
            # if it does, then send an email (implement using ses)
            return render_template("forgot_password_sent.html", email=email)
        # if it does not, stay on forgot password page and display error
        return render_template("forgot_password.html", error="Email not found")
    # render forgot password page
    return render_template("forgot_password.html")
# set route for chat select
@app.route("/select_chat")
def select_chat():
    # check that the user is logged in
    if "username" not in session:
        # if not, redirect back to login page
        return redirect(url_for("login"))
    # render select chat page
    return render_template("select_chat.html")
# set backend for selecting chat
@app.route("/start_chat", methods=["POST"])
def start_chat():
    # retrieve the user's username
    username = session.get("username")
    # if not logged in, redirect back to login page
    if not username:
        return redirect(url_for("login"))
    # retrieve user's choice
    choice = request.form.get("chat_choice")  # "continue" or "new"
    # set the s3 key using the user's username
    s3Key = f"{username}.json"
    # set the chat history appropriately
    with chatbotsLock:
        if choice=="continue" and s3Client.chatLookup(s3Key) is not None:
            history = s3Client.chatRead(s3Key)
        else:
            history = []
        # create a chatbot        
        userChatbots[username] = genaiClient.chats.create(
            model=os.environ["geminiModel"],
            history=history,
            config=config
        )
    return redirect(url_for("chat"))
# set route for chat
@app.route("/chat")
def chat():
    # check that user is logged in
    if "username" not in session:
        return redirect(url_for("login"))
    # get username when redirected to page
    username = session["username"]
    # render chat page
    return render_template("chat.html", username=username)
# set backend for sending messages to gemini
@app.route("/send", methods=["POST"])
def send_message():
    # check for user's chatbot
    username = session.get("username")
    if not username or username not in userChatbots:
        return jsonify({"error": "No active chat session"}), 403
    # load chatbot
    chatbot = userChatbots[username]
    # extract json for incoming request
    data = request.get_json()
    # extract message
    userInput = data.get("message", "")
    # check that the message is not empty
    if not userInput:
        # if it is, then return error
        return jsonify({"error": "Empty message"}), 400
    try:
        # if there is a message, try to send the message to chatbot
        response = chatbot.send_message(userInput)
        # write updated chat history to s3
        chatKey = f"{username}.json"
        chatHistory = chatbot.get_history()
        s3Client.chatWrite(chatKey, chatHistory)
        # return the response
        return jsonify({"response": response.text})
    # if there is an error while handling the message,
    except Exception as e:
        # return the error
        return jsonify({"error": str(e)}), 500
if __name__ == "__main__":
    app.run()