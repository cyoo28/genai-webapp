#!/usr/bin/env python3
# import flask related packages
from flask import Flask, render_template, request, redirect, url_for, jsonify
# import google related packages
from google import genai
from google.genai.types import GenerateContentConfig
# set up genai client
PROJECT_ID = "symphony-gce-dev-secops"
LOCATION = "us-east4"
MODEL_ID = "gemini-2.0-flash-001"
client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
# model instructions
system_instruction = """
  #You are a basic conversational assistant.
"""
# model configuration
config = GenerateContentConfig(
       system_instruction=system_instruction,
       temperature=0.4, # [float, 0.0, 1.0] control randomness/creativity of response (0.0 is more deterministic, 1.0 is more liberal)
        top_p=0.95, # [float, 0.0, 1.0] consider min set of most probable tokens that exceed cumulative probability, p, and then randomly pick from the next token from this smaller set
        top_k=20, # [int, 1, 1000] consider the k most probable next tokens and then randomly pick from the next token from this smaller set
        candidate_count=1, # [int, 1, 8] control how many candidate responses the model should generate that the user can choose from
        max_output_tokens=1000, # [int, 1, inf] limit maximum tokens contained in response
        presence_penalty=0.0, # [float, -2.0, 2.0] negative values discourage use of new tokens while positive values encourage use of new tokens
        frequency_penalty=0.0, # [float, -2.0, 2.0] negative values encourage repetition of tokens while positive values discourage repetition of tokens
       )
# initialize chat
chatbot = client.chats.create(
    model=MODEL_ID,
    history=[],
    config=config
    )
# set up flask webapp
app = Flask(__name__)
# list of users (eventually store in aws s3)
users = {
    'cyoo': {'password': 'cyoo', 'email': 'cyoo@ixcloudsecurity.com'},
    'iyoo': {'password': 'iyoo', 'email': 'iyoo@ixcloudsecurity.com'},
}
# set route for landing page
@app.route('/')
def index():
    # redirect to login page
    return redirect(url_for('login'))
# set route for login page
@app.route('/login', methods=['GET', 'POST'])
def login():
    # allow users to input username and password
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        # check that the username/password is a valid login
        if username in users and users[username]['password'] == password:
            # if it is, redirect to the chat
            return redirect(url_for('chat', username=username))
        else:
            # if not, stay on the login page and display error
            return render_template('login.html', error='Invalid username or password')
    # render the login page
    return render_template('login.html')
# set route for logout page
@app.route('/logout')
def logout():
    # render the logout page (which redirects to the login)
    return render_template('logout.html')
# set route for the signup page
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    # allow users to input username, password, and email
    if request.method == 'POST':
        new_username = request.form['username']
        new_password = request.form['password']
        new_email = request.form['email']
        confirm_email = request.form['confirm_email']
        # check for errors
        error = None
        # error if the username is already in use
        if new_username in users:
            error = 'Username already exists'
        # error if the email is already in use
        if any(user["email"] == new_email for user in users.values()):
            error = 'Email already in use'
        # error if the email confirmation does not match
        elif new_email != confirm_email:
            error = 'Emails do not match'
        # error if one of the fields is not filled out
        elif not new_username or not new_email or not new_password:
            error = 'Please enter a username, email, and password'
        # if there's an error
        if error:
            # stay on the signup page and display error
            return render_template('signup.html', error=error)
        else:
            # if no error, create new user
            users[new_username]['password'] = new_password
            users[new_username]['email'] = new_email
            # redirect to login page
            return redirect(url_for('login'))
    # render signup page
    return render_template('signup.html')
# set route for forgot password page
@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    # allow users to input email
    if request.method == 'POST':
        email = request.form['email']
        # check that the email exists in the userbase
        if any(user["email"] == email for user in users.values()):
            # if it does, then send an email (implement using ses)
            return render_template('forgot_password_sent.html', email=email)
        # if it does not, stay on forgot password page and display error
        return render_template('forgot_password.html', error='Email not found')
    # render forgot password page
    return render_template('forgot_password.html')
# set route for chat
@app.route('/chat')
def chat():
    # get username when redirected to page
    username = request.args.get('username')
    # render chat page
    return render_template('chat.html', username=username)
# set backend for sending messages to gemini
@app.route('/send', methods=['POST'])
def send_message():
    # extract json for incoming request
    data = request.get_json()
    # extract message
    user_input = data.get('message', '')
    # check that the message is not empty
    if not user_input:
        # if it is, then return error
        return jsonify({'error': 'Empty message'}), 400
    try:
        # if there is a message, try to send the message to chatbot
        response = chatbot.send_message(user_input)
        # return the response
        return jsonify({'response': response.text})
    # if there is an error while handling the message,
    except Exception as e:
        # return the error
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run()