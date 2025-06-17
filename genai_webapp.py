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
import secrets
from datetime import datetime, timedelta
import sys
import logging
from threading import Lock
# import custom files
from sqlClient import MySQLClient
from s3Client import MyS3Client
# define environment variables
os.environ["awsRegion"] = "us-east-1" # aws region for bot3 session
os.environ["gcpSecret"] = "gcp-genai-sa" # aws secret that contains gcp service account key
os.environ["gcpProject"] = "ix-sandbox" # gcp project where vertex ai resources are enabled
os.environ["gcpRegion"] = "us-east4" # gcp region where vertex ai resources are enabled
os.environ["geminiModel"] = "gemini-2.0-flash-001" # gemini model used for chatbot
os.environ["dbSecret"] = "mysql-db" # aws secret for MySQL db
os.environ["dbHost"] = os.environ.get("DBHOST", "genai-db.c16o2ig6ufoe.us-east-1.rds.amazonaws.com") # name of db host
os.environ["dbName"] = "genai" # name of MySQL database
os.environ["userTable"] = "users" # table with user info in MySQL database
os.environ["resetTable"] = "password_reset" # table with password reset tokens in MySQL database
os.environ["confirmTable"] = "confirmation" # table with confirmation tokens in MySQL database
os.environ["appParam"] = "webappKey" # aws parameter for webapp session key
os.environ["s3Bucket"] = "026090555438-genai-chat" # aws bucket for chat history
os.environ["emailSender"] = "notify@ixcloudsecurity.com" # sender for ses emails
# set up logging
logging.basicConfig(stream=sys.stderr, 
                    level=logging.INFO, 
                    format="%(asctime)-11s [%(levelname)s] %(message)s (%(name)s:%(lineno)d)")
logger = logging.getLogger(__name__)
# define function to send email
def send_email(sesClient, sender, recipients, subject, body):
    try:
        logger.debug(f"Sending notification to: {recipients}")
        charset = "UTF-8"
        res = sesClient.send_email(Destination={ "ToAddresses": recipients },
                                    Message={ "Body": { "Text": { "Charset": charset, "Data": body } },
                                              "Subject": { "Charset": charset, "Data": subject } },
                                    Source=sender)
        if "MessageId" in res:
            logger.info("Notification sent successfully: {}".format(res["MessageId"]))
        else:
            logger.warning("Notification may not have been sent: {}".format(res))
    except Exception as e:
        logger.error(f"Error sending email to {recipients}: {e}")
# define function to convert s3 object to model format
def chat_from_obj(chatObj):
    logger.debug(f"Converting s3 object to model format")
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
    logger.debug(f"Converting model format to s3 object")
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
# set up boto3 session
logger.debug(f"Setting up boto3")
awsProfile = os.environ.get("AWSPROF", None)
if awsProfile:
    awsSession = boto3.Session(profile_name=awsProfile, region_name=os.environ["awsRegion"])
else:
    awsSession = boto3.Session(region_name=os.environ["awsRegion"])
# set up aws clients
sesClient = awsSession.client("ses")
smClient = awsSession.client("secretsmanager")
ssmClient = awsSession.client("ssm")
# retrieve gcp service account key from aws secrets manager
logger.debug(f"Getting service account key from AWS Secrets Manager")
saResponse = smClient.get_secret_value(SecretId=os.environ["gcpSecret"])
saInfo = json.loads(saResponse["SecretString"])
# authenticate with gcloud service account
logger.debug(f"Authenticating GCP service account")
credentials = service_account.Credentials.from_service_account_info(
    saInfo,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
# set up genai client
logger.debug(f"Setting up genai client")
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
logger.debug(f"Getting database credentials from AWS Secrets Manager")
dbResponse = smClient.get_secret_value(SecretId=os.environ["dbSecret"])
dbInfo = json.loads(dbResponse["SecretString"])
logger.debug(f"Setting up SQL client")
sqlClient = MySQLClient(os.environ["dbHost"], dbInfo["username"], dbInfo["password"], os.environ["dbName"])
# create MyS3ChatHistory instance
logger.debug(f"Setting up s3 client")
s3Client = MyS3Client(awsSession, os.environ["s3Bucket"])
# retrieve webapp session key from aws parameter store
logger.debug(f"Getting webapp key from AWS Secrets Manager")
appResponse = ssmClient.get_parameter(Name=os.environ["appParam"], WithDecryption=True)
# set up flask webapp
logger.debug(f"Setting up webapp")
app = Flask(__name__)
appKey = appResponse["Parameter"]["Value"]
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
    users = sqlClient.read_table(os.environ["userTable"])
    # allow users to input username and password
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        # check that the username/password is a valid login
        user = next((u for u in users if u["username"] == username), None)
        # check for errors
        error = None
        # error if username or password is incorrect
        if not (user and bcrypt.checkpw(password.encode(), user["password"].encode())):
            error = "Invalid username or password"
        # error if user email has not been confirmed
        elif not user["confirmed"]:
            error = "User email has not been confirmed"
        # if there is an error,
        if error:
            # stay on the login page and display error
            logger.warning(f"Failed login attempt for user: {username}")
            return render_template("login.html", error=error)
        # otherwise, it is a successful login
        else:
            # set session
            session["username"] = username
            # session expires if browser is closed
            session.permanent = False
            # redirect to the chat
            logger.info(f"User {username} logged in.")
            return redirect(url_for("select_chat"))
    # render the login page
    error = request.args.get("error")
    return render_template("login.html", error=error)
# set route for logout page
@app.route("/logout")
def logout():
    username = session.get("username")
    logger.info(f"User {username} logged out.")
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
    users = sqlClient.read_table(os.environ["userTable"])
    # allow users to input username, password, and email
    if request.method == "POST":
        newUsername = request.form["username"]
        newPassword = request.form["password"]
        newEmail = request.form["email"]
        confirmEmail = request.form["confirmEmail"]
        # check for errors
        error = None
        # error if one of the fields is not filled out
        if not newUsername or not newEmail or not newPassword:
            error = "Please enter a username, email, and password"
        # error if the username is already in use
        elif any(newUsername == user["username"] for user in users):
            error = "Username already exists"
        # error if the email is already in use
        elif any(newEmail == user["email"] for user in users):
            error = "Email already in use"
        # error if the email confirmation does not match
        elif newEmail != confirmEmail:
            error = "Emails do not match"
        # if there's an error
        if error:
            logger.warning(f"Failed signup attempt for user: {newUsername}")
            # stay on the signup page and display error
            return render_template("signup.html", error=error)
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
            sqlClient.delete_entry(confirmDeleteFilter, os.environ["confirmTable"])
            # add new user to db
            sqlClient.add_entry(confirmEntry, os.environ["confirmTable"])
            sqlClient.add_entry(userEntry, os.environ["userTable"])
            # create confirmation link
            with app.app_context():
                confirmationUrl = url_for('confirm_email', token=token, _external=True)
            # make datetime more readable
            emailExpire = expire.strftime("%A, %B %d, %Y at %I:%M %p")
            # send email through SES
            sender = os.environ["emailSender"]
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
            send_email(sesClient, sender, recipient, subject, body)
            logger.info(f"User {newUsername} signed up.")
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
    confirmTokens = sqlClient.read_table(os.environ["confirmTable"])
    # check that the reset token exists
    tokenEntry = next((row for row in confirmTokens if row["token"] == token), None)
    # error if no token is in the url or it doesn't exist in the table or if the token is expired
    if not token or not tokenEntry or datetime.now() > tokenEntry["expiration"]:
        logger.warning(f"Failed email confirmation attempt for user: {tokenEntry['username']}")
        # stay on the signup page and display error
        return redirect(url_for("login", error="Email confirmation has expired. Try signing up again"))
    # format user information into dict with sql columns as keys
    userUpdateValue = {"confirmed": True}
    userUpdateFilter = {"username": tokenEntry["username"]}
    resetDeleteFilter = {"token": token}
    # update confirmation status for user in db
    sqlClient.update_entry(userUpdateValue, userUpdateFilter, os.environ["userTable"])
    # delete the token after successful reset
    sqlClient.delete_entry(resetDeleteFilter, os.environ["confirmTable"])
    logger.info(f"User {tokenEntry['username']} confirmed email")
    # render success page
    return render_template("confirm_email_success.html")
# set route for forgot password page
@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    # get users from rds
    users = sqlClient.read_table(os.environ["userTable"])
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
            sqlClient.delete_entry(resetDeleteFilter, os.environ["resetTable"])
            # format reset information into dict with sql columns as keys
            resetEntry = {"username": matchedUser["username"], "token": token, "expiration": sqlExpire}
            # add new reset info
            sqlClient.add_entry(resetEntry, os.environ["resetTable"])
            # send an email (implement using ses)
            with app.app_context():
                resetUrl = url_for('reset_password', token=token, _external=True)
            # make datetime more readable
            emailExpire = expire.strftime("%A, %B %d, %Y at %I:%M %p")
            # send email through SES
            sender = os.environ["emailSender"]
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
            send_email(sesClient, sender, recipient, subject, body)
            # render forgot password success page
            logger.info(f"User {matchedUser['username']} requested password reset.")
            return render_template("forgot_password_success.html", email=email)
        # if it does not,
        else:
            # stay on forgot password page and display error
            logger.warning(f"Failed password reset request for user: {matchedUser['username']}")
            return render_template("forgot_password.html", error="Email not found")
    # render forgot password page
    return render_template("forgot_password.html")
# set route for reset password page
@app.route("/reset_password", methods=["GET", "POST"])
def reset_password():
    # get token from url
    token = request.args.get("token")
    # get reset tokens from rds
    resetTokens = sqlClient.read_table(os.environ["resetTable"])
    # check that the reset token exists
    tokenEntry = next((row for row in resetTokens if row["token"] == token), None)
    # error if no token is in the url or it doesn't exist in the table or if the token is expired
    if not token or not tokenEntry or datetime.now() > tokenEntry["expiration"]:
        logger.warning(f"Failed password reset attempt for user: {tokenEntry['username']}")
        # stay on the signup page and display error
        return redirect(url_for("login", error="Password reset has expired. Try requesting again"))
    # if no error,
    if request.method == "POST":
        # allow users to input new password
        newPassword = request.form["password"]
        confirmPassword = request.form["confirmPassword"]
        # check for errors
        error = None
        # error if one of the fields is not filled out
        if not newPassword or not confirmPassword:
            error = "Please enter email and confirmation"
        # error if the email confirmation does not match
        elif newPassword != confirmPassword:
            error = "Passwords do not match"
        # if there's an error
        if error:
            # stay on the signup page and display error
            return redirect(url_for("login", error=error))
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
            sqlClient.update_entry(userUpdateValue, userUpdateFilter, os.environ["userTable"])
            # delete the token after successful reset
            sqlClient.delete_entry(resetDeleteFiler, os.environ["resetTable"])
            logger.info(f"User {tokenEntry['username']} reset password.")
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
        return redirect(url_for("login"))
    # get users from rds
    users = sqlClient.read_table(os.environ["userTable"])
    # get username when redirected to page
    username = session["username"]
    user = next((u for u in users if u["username"] == username), None)
    email = user["email"]
    if request.method == "POST":
        oldPassword = request.form["oldPassword"]
        newPassword = request.form["newPassword"]
        confirmPassword = request.form["confirmPassword"]
        # check for errors
        error = None
        # error if the current password is incorrect
        if not bcrypt.checkpw(oldPassword.encode(), user["password"].encode()):
            error = "Incorrect current password."
        # error if the email confirmation does not match
        if newPassword != confirmPassword:
            error = "change_password.html"
        if error:
            logger.warning(f"Failed password change attempt for user: {username}")
            return render_template('change_password.html', error=error)
        else:
            # generate salt to encrypt password
            salt = bcrypt.gensalt()
            # encrypt password
            hashedPassword = bcrypt.hashpw(newPassword.encode(), salt)
            # format change information into dict with sql columns as keys
            userUpdateValue  = {"password": hashedPassword}
            userUpdateFilter = {"username": username}
            # update password for user in db
            sqlClient.update_entry(userUpdateValue, userUpdateFilter, os.environ["userTable"])
            logger.info(f"User {username} changed password.")
            # redirect to login page
            return redirect(url_for("select_chat"))
    return render_template("change_password.html")
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
    s3Key = f"chat-history/{username}.json"
    # set the chat history appropriately
    with chatbotsLock:
        if choice=="continue" and s3Client.obj_lookup(s3Key) is not None:
            logger.info(f"User {username} is continuing an old chat.")
            chatObj = s3Client.obj_read(s3Key)
            history = chat_from_obj(chatObj)
        else:
            logger.info(f"User {username} has started a new chat.")
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
    logger.debug(f"User {username} sent message: {userInput}")
    # check that the message is not empty
    if not userInput:
        # if it is, then return error
        return jsonify({"error": "Empty message"}), 400
    try:
        # if there is a message, try to send the message to chatbot
        response = chatbot.send_message(userInput)
        logger.debug(f"Model response for {username}: {response.text}")
        # write updated chat history to s3
        chatKey = f"chat-history/{username}.json"
        chatHistory = chatbot.get_history()
        chatObj = chat_to_obj(chatHistory)
        s3Client.obj_write(chatKey, chatObj, "application/json")
        # return the response
        return jsonify({"response": response.text})
    # if there is an error while handling the message,
    except Exception as e:
        logger.error(f"Error processing message for {username}: {e}", exc_info=True)
        # return the error
        return jsonify({"error": str(e)}), 500
# create a global error handler
@app.errorhandler(Exception)
def handle_exception(e):
    logger.error("Unhandled exception occurred", exc_info=True)
    return jsonify({"error": "An internal server error occurred"}), 500
@app.route("/ping")
def ping():
    return "pong"

if __name__ == "__main__":
    logger.info("Starting Flask app on http://127.0.0.1:5000")
    app.run()