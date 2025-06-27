#!/usr/bin/env python3
# import packages
import os
import json
import sys
import logging
import secrets
from datetime import datetime, timedelta
from time import time, sleep
from threading import Thread, Lock
from flask import Flask, render_template, request, redirect, url_for, jsonify, session
import bcrypt
import boto3
from google import genai
from google.genai.types import (
    GenerateContentConfig,
    Tool,
    Retrieval,
    VertexRagStore,
)
from google.oauth2 import service_account
# import custom files
from sqlClient import MySQLClient
from s3Client import MyS3Client
# configure logger
logging.basicConfig(stream=sys.stderr, 
                    level=logging.INFO, 
                    format="%(asctime)-11s [%(levelname)s] %(message)s (%(name)s:%(lineno)d)")
# initialize global variables
class Config:
    configStore = dict()
    @classmethod
    def create(cls):
        if not cls.configStore:
            # create logger
            cls.configStore["logger"] = logging.getLogger(__name__)
            # set up boto3 session
            cls.configStore["logger"].debug(f"Setting up boto3")
            awsProfile = os.environ.get("AWSPROF", None)
            if awsProfile:
                awsSession = boto3.Session(profile_name=awsProfile, region_name="us-east-1")
            else:
                awsSession = boto3.Session(region_name="us-east-1")
            # set up boto3 clients
            ssmClient = awsSession.client("ssm")
            smClient = awsSession.client("secretsmanager")
            cls.configStore["sesClient"] = awsSession.client("ses")
            # get variables from parameter store
            cls.configStore["gcpSecret"] = ssmClient.get_parameter(Name="/genai/gcpSecret", WithDecryption=True)["Parameter"]["Value"] # aws secret that contains gcp service account key
            cls.configStore["gcpProject"] = ssmClient.get_parameter(Name="/genai/gcpProject", WithDecryption=True)["Parameter"]["Value"] # gcp project where vertex ai resources are enabled
            cls.configStore["gcpRegion"] = ssmClient.get_parameter(Name="/genai/gcpRegion", WithDecryption=True)["Parameter"]["Value"] # gcp region where vertex ai resources are enabled
            cls.configStore["geminiModel"] = ssmClient.get_parameter(Name="/genai/geminiModel", WithDecryption=True)["Parameter"]["Value"] # gemini model used for chatbot
            cls.configStore["dbSecret"] = ssmClient.get_parameter(Name="/genai/dbSecret", WithDecryption=True)["Parameter"]["Value"] # aws secret for MySQL db
            cls.configStore["dbHost"] = os.environ.get("DBHOST", ssmClient.get_parameter(Name="/genai/dbHost", WithDecryption=True)["Parameter"]["Value"]) # name of db host
            cls.configStore["dbName"] = ssmClient.get_parameter(Name="/genai/dbName", WithDecryption=True)["Parameter"]["Value"] # name of MySQL database
            cls.configStore["userTable"] = ssmClient.get_parameter(Name="/genai/userTable", WithDecryption=True)["Parameter"]["Value"] # table with user info in MySQL database
            cls.configStore["resetTable"] = ssmClient.get_parameter(Name="/genai/resetTable", WithDecryption=True)["Parameter"]["Value"] # table with password reset tokens in MySQL database
            cls.configStore["confirmTable"] = ssmClient.get_parameter(Name="/genai/confirmTable", WithDecryption=True)["Parameter"]["Value"] # table with confirmation tokens in MySQL database
            cls.configStore["appParam"] = ssmClient.get_parameter(Name="/genai/appParam", WithDecryption=True)["Parameter"]["Value"] # aws parameter for webapp session key
            cls.configStore["s3Bucket"] = ssmClient.get_parameter(Name="/genai/s3Bucket", WithDecryption=True)["Parameter"]["Value"] # aws bucket for chat history
            cls.configStore["emailSender"] = ssmClient.get_parameter(Name="/genai/emailSender", WithDecryption=True)["Parameter"]["Value"] # sender for ses emails
            cls.configStore["corpusId"] = ssmClient.get_parameter(Name="/genai/corpusId/test", WithDecryption=True)["Parameter"]["Value"] # test corpus id for RAG
            #cls.configStore["corpusId"] =  ssmClient.get_parameter(Name="/genai/corpusId/prod", WithDecryption=True)["Parameter"]["Value"] # prod corpus id for RAG
            # create MySQLClient instance in aws
            cls.configStore["logger"].debug(f"Getting database credentials from AWS Secrets Manager")
            dbResponse = smClient.get_secret_value(SecretId=cls.configStore["dbSecret"])
            dbInfo = json.loads(dbResponse["SecretString"])
            cls.configStore["logger"].debug(f"Setting up SQL client")
            cls.configStore["sqlClient"] = MySQLClient(cls.configStore["dbHost"], dbInfo["username"], dbInfo["password"], cls.configStore["dbName"])
            # create MyS3ChatHistory instance
            cls.configStore["logger"].debug(f"Setting up s3 client")
            cls.configStore["s3Client"] = MyS3Client(awsSession, cls.configStore["s3Bucket"])
            # retrieve gcp service account key from aws secrets manager
            cls.configStore["logger"].debug(f"Getting service account key from AWS Secrets Manager")
            saResponse = smClient.get_secret_value(SecretId=cls.configStore["gcpSecret"])
            saInfo = json.loads(saResponse["SecretString"])
            # authenticate with gcloud service account
            cls.configStore["logger"].debug(f"Authenticating GCP service account")
            credentials = service_account.Credentials.from_service_account_info(
                saInfo,
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
            # set up genai client
            cls.configStore["logger"].debug(f"Setting up genai client")
            cls.configStore["genaiClient"] = genai.Client(vertexai=True, project=cls.configStore["gcpProject"], location=cls.configStore["gcpRegion"], credentials=credentials)
            # set up chatbot instructions
            chatbotInstruction = """
                You are a warm, empathetic, conversational assistant who listens attentively and encourages the user to share about their day and experiences.
                As you chat, naturally guide the conversation to capture key details such as:
                - Where the user was
                - What the user did
                - Who they spent time with
                - How they felt about the experience
                Do this in a gentle, casual manner. Avoid direct, pointed questions or sounding like an interviewer. Instead, reflect on what the user says and respond in ways that invite them to share more about these important aspects.
                
                Since this is for journaling, it is very important to get information about when it occurred.
                If the user uses vague date terms (e.g. Last week, Last time), try to get a more specific date indicator.
                This does not need to be an explicit date but can also be relative terms based on today's date (e.g. Today, Last Sunday).

                You also have access to a memory database of the user's past experiences.
                Use it to recall and reference memories when they are meaningfully related to what the user is saying. For example:
                - If the user mentions a person, place, or activity they’ve mentioned before
                - If today’s experience is similar to a past event
                - If events seem connected (e.g. same day, recurring themes, patterns)

                Bring up these past memories in a natural, thoughtful way — as if you remember them. This helps the user reflect, feel understood, and recognize connections in their own story.
                Use memory context sparingly and seamlessly. Never break the conversational tone or sound like you're querying data. Your goal is to help the user express themselves freely while building a rich, meaningful memory profile over time.
                Keep your tone friendly, supportive, and open-ended.
                """
            # set up rag
            corpus = VertexRagStore(
                rag_corpora=[cls.configStore["corpusId"]],
                similarity_top_k=10,
                vector_distance_threshold=0.5,
            )
            ragTool = Tool(
                retrieval=Retrieval(vertex_rag_store=corpus)
            )
            # set up configuration for chatbot model
            cls.configStore["chatbotConfig"] = GenerateContentConfig(
                system_instruction=chatbotInstruction,
                temperature=0.4, # [float, 0.0, 1.0] control randomness/creativity of response (0.0 is more deterministic, 1.0 is more liberal)
                    top_p=0.95, # [float, 0.0, 1.0] consider min set of most probable tokens that exceed cumulative probability, p, and then randomly pick from the next token from this smaller set
                    top_k=40, # [int, 1, 1000] consider the k most probable next tokens and then randomly pick from the next token from this smaller set
                    max_output_tokens=800, # [int, 1, inf] limit maximum tokens contained in response
                    presence_penalty=0.4, # [float, -2.0, 2.0] negative values discourage use of new tokens while positive values encourage use of new tokens
                    frequency_penalty=0.2, # [float, -2.0, 2.0] negative values encourage repetition of tokens while positive values discourage repetition of tokens
                    tools=[ragTool]
                )
            # In-memory store for chatbots keyed by username
            cls.configStore["userChatbots"] = {}
            cls.configStore["chatbotsLock"] = Lock()
            cls.configStore["lastMessageTime"] = {}
            cls.configStore["chatbotLastUsed"] = {}
        return cls.configStore
Config.create()
# define function to send email
def send_email(sesClient, sender, recipients, subject, body):
    try:
        app.config["Config"]["logger"].debug(f"Sending notification to: {recipients}")
        charset = "UTF-8"
        res = sesClient.send_email(Destination={ "ToAddresses": recipients },
                                    Message={ "Body": { "Text": { "Charset": charset, "Data": body } },
                                              "Subject": { "Charset": charset, "Data": subject } },
                                    Source=sender)
        if "MessageId" in res:
            app.config["Config"]["logger"].info("Notification sent successfully: {}".format(res["MessageId"]))
        else:
            app.config["Config"]["logger"].warning("Notification may not have been sent: {}".format(res))
    except Exception as e:
        app.config["Config"]["logger"].error(f"Error sending email to {recipients}: {e}")
# define function to convert s3 object to model format
def chat_from_obj(chatObj):
    app.config["Config"]["logger"].debug(f"Converting s3 object to model format")
    # convert s3 object to json
    chatJson = json.loads(chatObj)
    # convert json format into model format
    chatHistory = []
    for message in chatJson:
        parts = [{"text": part} for part in message["parts"]]
        chatHistory.append({
            "role": message["role"],
            "parts": parts
        })
    return chatHistory
# define function to convert chat to s3 object
def chat_to_obj(chatHistory):
    app.config["Config"]["logger"].debug(f"Converting model format to s3 object")
    # convert model format into json format
    chatJson = []
    for message in chatHistory:
        parts = [part.text for part in message.parts]
        chatJson.append({
            "role": message.role,
            "parts": parts
        })
    # convert json to object for s3
    chatObj = json.dumps(chatJson)
    return chatObj
# define function to cleanup idle chatbots
def chatbot_cleanup():
    while True:
        with app.config["Config"]["chatbotsLock"]:
            now = time()
            timeout = app.permanent_session_lifetime.total_seconds()
            inactive_users = [
                username for username, last_used in app.config["Config"]["chatbotLastUsed"].items()
                if now - last_used > timeout
            ]
            for username in inactive_users:
                app.config["Config"]["userChatbots"].pop(username, None)
                app.config["Config"]["chatbotLastUsed"].pop(username, None)
                app.config["Config"]["lastMessageTime"].pop(username, None)
                app.config["Config"]["logger"].info(f"Removed inactive chatbot for user: {username}")
        sleep(1200)  # Run every 20 min
# set up flask webapp
app = Flask(__name__)
app.config["Config"] = Config.configStore
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30) # time out session after 30 minutes
app.secret_key = os.urandom(32)
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
    errorMessage = None
    # get users from rds
    users = app.config["Config"]["sqlClient"].read_table(app.config["Config"]["userTable"])
    # allow users to input username and password
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        # check that the username/password is a valid login
        user = next((u for u in users if u["username"] == username), None)
        # error if username or password is incorrect
        if not (user and bcrypt.checkpw(password.encode(), user["password"].encode())):
            errorMessage = "Invalid username or password"
        # error if user email has not been confirmed
        elif not user["confirmed"]:
            errorMessage = "User email has not been confirmed"
        # if there is an error,
        if errorMessage:
            # stay on the login page and display error
            app.config["Config"]["logger"].warning(f"Failed login attempt for user: {username}")
            return render_template("login.html", error=errorMessage)
        # otherwise, it is a successful login
        else:
            # set session
            session["username"] = username
            # session expires if browser is closed
            session.permanent = True
            # redirect to the chat
            app.config["Config"]["logger"].info(f"User {username} logged in.")
            return redirect(url_for("select_chat"))
    # render the login page
    error = request.args.get("error")
    if error == "session_expired":
        errorMessage = "Your session has expired due to inactivity. Please log in again."
    elif error == "confirm_expired":
        errorMessage = "Email confirmation has expired. Try signing up again."
    elif error == "reset_expired":
        errorMessage = "Password reset has expired. Try requesting again."
    return render_template("login.html", error=errorMessage)
# set route for logout page
@app.route("/logout")
def logout():
    username = session.get("username")
    if username:
        app.config["Config"]["logger"].info(f"User {username} logged out.")
        # delete chatbot
        with app.config["Config"]["chatbotsLock"]:
            app.config["Config"]["userChatbots"].pop(username, None)
            app.config["Config"]["chatbotLastUsed"].pop(username, None)
            app.config["Config"]["lastMessageTime"].pop(username, None)
    else:
        app.config["Config"]["logger"].info("User logged out but no username was in session.")
    # clear session
    session.clear()
    # render the logout page (which redirects to the login)
    return render_template("logout.html")
# set route for the signup page
@app.route("/signup", methods=["GET", "POST"])
def signup():
    # get users from rds
    users = app.config["Config"]["sqlClient"].read_table(app.config["Config"]["userTable"])
    # allow users to input username, password, and email
    if request.method == "POST":
        newUsername = request.form["username"]
        newPassword = request.form["password"]
        newEmail = request.form["email"]
        confirmEmail = request.form["confirmEmail"]
        # check for errors
        errorMessage = None
        # error if one of the fields is not filled out
        if not newUsername or not newEmail or not newPassword:
            errorMessage = "Please enter a username, email, and password"
        # error if the username is already in use
        elif any(newUsername == user["username"] for user in users):
            errorMessage = "Username already exists"
        # error if the email is already in use
        elif any(newEmail == user["email"] for user in users):
            errorMessage = "Email already in use"
        # error if the email confirmation does not match
        elif newEmail != confirmEmail:
            errorMessage = "Emails do not match"
        # if there's an error
        if errorMessage:
            app.config["Config"]["logger"].warning(f"Failed signup attempt for user: {newUsername}")
            # stay on the signup page and display error
            return render_template("signup.html", error=errorMessage)
        # if no error,
        else:
            # generate salt to encrypt password
            salt = bcrypt.gensalt()
            # encrypt password
            hashedPassword = bcrypt.hashpw(newPassword.encode(), salt)
            # generate a confirmation token
            token = secrets.token_urlsafe(32)
            # and set its expiration date
            expire = datetime.now() + timedelta(days=1)
            sqlExpire = expire.strftime('%Y-%m-%d %H:%M:%S')
            # format user into dict with sql columns as keys
            userEntry = {"username": newUsername, "password": hashedPassword, "email": newEmail, "confirmed": False}
            confirmEntry = {"username": newUsername, "token": token, "expiration": sqlExpire}
            confirmDeleteFilter = {"username": newUsername}
            # remove any existing signup info
            app.config["Config"]["sqlClient"].delete_entry(confirmDeleteFilter, app.config["Config"]["confirmTable"])
            # add new user to db
            app.config["Config"]["sqlClient"].add_entry(confirmEntry, app.config["Config"]["confirmTable"])
            app.config["Config"]["sqlClient"].add_entry(userEntry, app.config["Config"]["userTable"])
            # create confirmation link
            with app.app_context():
                confirmationUrl = url_for('confirm_email', token=token, _external=True)
            # make datetime more readable
            emailExpire = expire.strftime("%A, %B %d, %Y at %I:%M %p")
            # send email through SES
            sender = app.config["Config"]["emailSender"]
            recipient = [newEmail]
            subject = "IX Cloud Signup Confirmation"
            body = f"""Hello {newUsername},

                Thank you for signing up! Please confirm your email address by clicking the link below:

                {confirmationUrl}

                This link will expire in 24 hours (at {emailExpire}).

                If you did not sign up for this account, please ignore this email.

                Best regards,  
                IX Cloud Security Team
            """
            send_email(app.config["Config"]["sesClient"], sender, recipient, subject, body)
            app.config["Config"]["logger"].info(f"User {newUsername} signed up.")
            # render signup page success page
            return render_template("signup_success.html")
    # render signup page
    return render_template("signup.html")
# set route for email confirmation page
@app.route("/confirm_email")
def confirm_email():
    # get token from url
    token = request.args.get("token")
    # get confirmation tokens from rds
    confirmTokens = app.config["Config"]["sqlClient"].read_table(app.config["Config"]["confirmTable"])
    # check that the reset token exists
    tokenEntry = next((row for row in confirmTokens if row["token"] == token), None)
    # error if no token is in the url or it doesn't exist in the table or if the token is expired
    if not token or not tokenEntry or datetime.now() > tokenEntry["expiration"]:
        app.config["Config"]["logger"].warning(f"Failed email confirmation attempt for user: {tokenEntry['username']}")
        # redirect to login page and display error
        return redirect(url_for("login", error="confirm_expired"))
    # format user information into dict with sql columns as keys
    userUpdateValue = {"confirmed": True}
    userUpdateFilter = {"username": tokenEntry["username"]}
    resetDeleteFilter = {"token": token}
    # update confirmation status for user in db
    app.config["Config"]["sqlClient"].update_entry(userUpdateValue, userUpdateFilter, app.config["Config"]["userTable"])
    # delete the token after successful reset
    app.config["Config"]["sqlClient"].delete_entry(resetDeleteFilter, app.config["Config"]["confirmTable"])
    app.config["Config"]["logger"].info(f"User {tokenEntry['username']} confirmed email")
    # render success page
    return render_template("confirm_email_success.html")
# set route for forgot password page
@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    # get users from rds
    users = app.config["Config"]["sqlClient"].read_table(app.config["Config"]["userTable"])
    # allow users to input email
    if request.method == "POST":
        email = request.form["email"]
        # find the user with the matching email
        matchedUser = next((user for user in users if user["email"] == email), None)
        # if the email exists in the userbase
        if matchedUser:
            # generate a reset token
            token = secrets.token_urlsafe(32)
            # and set its expiration date
            expire = datetime.now() + timedelta(minutes=10)
            sqlExpire = expire.strftime('%Y-%m-%d %H:%M:%S')
            # format user into dict with sql columns as keys
            resetDeleteFilter = {"username": matchedUser["username"]}
            # remove any existing reset info
            app.config["Config"]["sqlClient"].delete_entry(resetDeleteFilter, app.config["Config"]["resetTable"])
            # format reset information into dict with sql columns as keys
            resetEntry = {"username": matchedUser["username"], "token": token, "expiration": sqlExpire}
            # add new reset info
            app.config["Config"]["sqlClient"].add_entry(resetEntry, app.config["Config"]["resetTable"])
            # send an email (implement using ses)
            with app.app_context():
                resetUrl = url_for('reset_password', token=token, _external=True)
            # make datetime more readable
            emailExpire = expire.strftime("%A, %B %d, %Y at %I:%M %p")
            # send email through SES
            sender = app.config["Config"]["emailSender"]
            recipient = [email]
            subject = "IX Cloud Password Reset Request"
            body = f"""Hello {matchedUser["username"]},

                Please reset your account password by clicking the link below:

                {resetUrl}

                This link will expire in 10 minutes (at {emailExpire}).

                If you did not request to reset your password, please ignore this email.

                Best regards,  
                IX Cloud Security Team
            """
            send_email(app.config["Config"]["sesClient"], sender, recipient, subject, body)
            # render forgot password success page
            app.config["Config"]["logger"].info(f"User {matchedUser['username']} requested password reset.")
            return render_template("forgot_password_success.html", email=email)
        # if it does not,
        else:
            # stay on forgot password page and display error
            app.config["Config"]["logger"].warning(f"Failed password reset request for user: {matchedUser['username']}")
            return render_template("forgot_password.html", error="Email not found")
    # render forgot password page
    return render_template("forgot_password.html")
# set route for reset password page
@app.route("/reset_password", methods=["GET", "POST"])
def reset_password():
    # get token from url
    token = request.args.get("token")
    # get reset tokens from rds
    resetTokens = app.config["Config"]["sqlClient"].read_table(app.config["Config"]["resetTable"])
    # check that the reset token exists
    tokenEntry = next((row for row in resetTokens if row["token"] == token), None)
    # error if no token is in the url or it doesn't exist in the table or if the token is expired
    if not token or not tokenEntry or datetime.now() > tokenEntry["expiration"]:
        app.config["Config"]["logger"].warning(f"Failed password reset attempt for user: {tokenEntry['username']}")
        # redirect to login page and display error
        return redirect(url_for("login", error="reset_expired"))
    # if no error,
    if request.method == "POST":
        # allow users to input new password
        newPassword = request.form["password"]
        confirmPassword = request.form["confirmPassword"]
        # check for errors
        errorMessage = None
        # error if one of the fields is not filled out
        if not newPassword or not confirmPassword:
            errorMessage = "Please enter email and confirmation"
        # error if the email confirmation does not match
        elif newPassword != confirmPassword:
            errorMessage = "Passwords do not match"
        # if there's an error
        if errorMessage:
            # stay on the signup page and display error
            return render_template("reset_password.html", error=errorMessage)
        # if no error
        else:
            # generate salt to encrypt password
            salt = bcrypt.gensalt()
            # encrypt password
            hashedPassword = bcrypt.hashpw(newPassword.encode(), salt)
            # format reset information into dict with sql columns as keys
            userUpdateValue = {"password": hashedPassword}
            userUpdateFilter = {"username": tokenEntry["username"]}
            resetDeleteFiler = {"token": token}
            # update password for user in db
            app.config["Config"]["sqlClient"].update_entry(userUpdateValue, userUpdateFilter, app.config["Config"]["userTable"])
            # delete the token after successful reset
            app.config["Config"]["sqlClient"].delete_entry(resetDeleteFiler, app.config["Config"]["resetTable"])
            app.config["Config"]["logger"].info(f"User {tokenEntry['username']} reset password.")
            # render success page
            return render_template("reset_password_success.html")
    # render reset password page
    return render_template("reset_password.html")
# set route for change password page
@app.route("/change_password", methods=["GET", "POST"])
def change_password():
    # check that the user is logged in
    if "username" not in session:
        # if not, redirect back to login page
        return redirect(url_for("login", error="session_expired"))
    # get users from rds
    users = app.config["Config"]["sqlClient"].read_table(app.config["Config"]["userTable"])
    # get username when redirected to page
    username = session["username"]
    user = next((u for u in users if u["username"] == username), None)
    email = user["email"]
    if request.method == "POST":
        oldPassword = request.form["oldPassword"]
        newPassword = request.form["newPassword"]
        confirmPassword = request.form["confirmPassword"]
        # check for errors
        errorMessage = None
        # error if the current password is incorrect
        if not bcrypt.checkpw(oldPassword.encode(), user["password"].encode()):
            errorMessage = "Incorrect current password."
        # error if the email confirmation does not match
        if newPassword != confirmPassword:
            errorMessage = "Passwords do not match"
        if errorMessage:
            app.config["Config"]["logger"].warning(f"Failed password change attempt for user: {username}")
            return render_template('change_password.html', error=errorMessage)
        else:
            # generate salt to encrypt password
            salt = bcrypt.gensalt()
            # encrypt password
            hashedPassword = bcrypt.hashpw(newPassword.encode(), salt)
            # format change information into dict with sql columns as keys
            userUpdateValue  = {"password": hashedPassword}
            userUpdateFilter = {"username": username}
            # update password for user in db
            app.config["Config"]["sqlClient"].update_entry(userUpdateValue, userUpdateFilter, app.config["Config"]["userTable"])
            app.config["Config"]["logger"].info(f"User {username} changed password.")
            # redirect to login page
            return redirect(url_for("select_chat"))
    return render_template("change_password.html")
# set route for chat select
@app.route("/select_chat")
def select_chat():
    # check that the user is logged in
    if "username" not in session:
        # if not, redirect back to login page
        return redirect(url_for("login", error="session_expired"))
    # render select chat page
    return render_template("select_chat.html")
# set backend for selecting chat
@app.route("/start_chat", methods=["POST"])
def start_chat():
    # retrieve the user's username
    username = session.get("username")
    # if not logged in, redirect back to login page
    if not username:
        return redirect(url_for("login", error="session_expired"))
    # retrieve user's choice
    choice = request.form.get("chat_choice")  # "continue" or "new"
    # set the s3 key using the user's username
    s3Key = f"chat-history/{username}.json"
    # set the chat history appropriately
    with app.config["Config"]["chatbotsLock"]:
        history = []
        if choice=="continue":
            try:
                if app.config["Config"]["s3Client"].obj_lookup(s3Key):
                    app.config["Config"]["logger"].info(f"User {username} is continuing an old chat.")
                    chatObj = app.config["Config"]["s3Client"].obj_read(s3Key)
                    history = chat_from_obj(chatObj)
                else:
                    app.config["Config"]["logger"].info(f"No previous chat found for {username}. Starting fresh.")
            except Exception as e:
                app.config["Config"]["logger"].warning(f"Failed to load chat history for {username}: {e}")
        else:
            app.config["Config"]["logger"].info(f"User {username} has started a new chat.")
        # create a chatbot        
        app.config["Config"]["userChatbots"][username] = app.config["Config"]["genaiClient"].chats.create(
            model=app.config["Config"]["geminiModel"],
            history=history,
            config=app.config["Config"]["chatbotConfig"]
        )
    # Set last message time
    app.config["Config"]["lastMessageTime"][username] = time()
    app.config["Config"]["chatbotLastUsed"][username] = time()
    return redirect(url_for("chat"))
# set route for chat
@app.route("/chat")
def chat():
    # check that user is logged in
    if "username" not in session:
        return redirect(url_for("login", error="session_expired"))
    # get username when redirected to page
    username = session["username"]
    # render chat page
    return render_template("chat.html", username=username)
# set backend for sending messages to gemini
@app.route("/send", methods=["POST"])
def send_message():
    # check for user's chatbot
    username = session.get("username")
    if not username or username not in app.config["Config"]["userChatbots"]:
        return jsonify({"error": "Chat session has expired."}), 403
    # load chatbot
    chatbot = app.config["Config"]["userChatbots"][username]
    # extract json for incoming request
    data = request.get_json()
    # extract message
    userInput = data.get("message", "").strip()
    app.config["Config"]["logger"].debug(f"User {username} sent message: {userInput}")
    # Reject empty messages
    if not userInput:
        return jsonify({"error": "Empty message"}), 400
    # Enforce max message length
    maxLength = 2000
    if len(userInput) > maxLength:
        return jsonify({"error": f"Message too long. Limit is {maxLength} characters."}), 400
    # Enforce rate limit in seconds (e.g., 1 message every 2 seconds)
    rateLimit = 2
    now = time()
    lastTime = app.config["Config"]["lastMessageTime"].get(username)
    if now - lastTime < rateLimit:
        return jsonify({"error": f"You're sending messages too fast. Please wait a moment."}), 429
    # update last message time
    app.config["Config"]["lastMessageTime"][username] = now
    app.config["Config"]["chatbotLastUsed"][username] = now
    try:
        # if there is a message, try to send the message to chatbot
        response = chatbot.send_message(userInput)
        app.config["Config"]["logger"].debug(f"Model response for {username}: {response.text}")
        # write updated chat history to s3
        chatKey = f"chat-history/{username}.json"
        chatHistory = chatbot.get_history()
        chatObj = chat_to_obj(chatHistory)
        app.config["Config"]["s3Client"].obj_write(chatKey, chatObj, "application/json")
        # return the response
        return jsonify({"response": response.text})
    # if there is an error while handling the message,
    except Exception as e:
        app.config["Config"]["logger"].error(f"Error processing message for {username}: {e}", exc_info=True)
        # return the error
        return jsonify({"error": str(e)}), 500
# create a global error handler
@app.errorhandler(Exception)
def handle_exception(e):
    app.config["Config"]["logger"].error("Unhandled exception occurred", exc_info=True)
    return jsonify({"error": "An internal server error occurred"}), 500
@app.route("/ping")
def ping():
    return "pong"

if __name__ == "__main__":
    # Start background thread for cleaning up inactive chatbots
    Thread(target=chatbot_cleanup, daemon=True).start()
    # Start webapp
    app.config["Config"]["logger"].info("Starting Flask app on http://127.0.0.1:5000")
    app.run()