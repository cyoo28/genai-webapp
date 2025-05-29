#!/usr/bin/env python3
from flask import Flask, render_template, request, redirect, url_for, jsonify
import time

from google import genai
from google.genai.types import (
    CreateCachedContentConfig,
    EmbedContentConfig,
    FunctionDeclaration,
    GenerateContentConfig,
    Part,
    SafetySetting,
    Tool,
)
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

users = {
    'cyoo': 'cyoo'
}
user_emails = {
    'cyoo': 'cyoo@ixcloudsecurity.com',
}

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in users and users[username] == password:
            return redirect(url_for('chat', username=username))
        else:
            return render_template('login.html', error='Invalid username or password')
    return render_template('login.html')

@app.route('/logout')
def logout():
    return render_template('logout.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        new_username = request.form['username']
        new_password = request.form['password']
        new_email = request.form['email']
        confirm_email = request.form['confirm_email']
        error = None
        if new_username in users:
            error = 'Username already exists'
        elif new_email != confirm_email:
            error = 'Emails do not match'
        elif not new_username or not new_email or not new_password:
            error = 'Please enter a username, email, and password'
        if error:
            return render_template('signup.html', error=error)
        else:
            users[new_username] = new_password
            user_emails[new_username] = new_email
            return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        for username, user_email in user_emails.items():
            if user_email == email:
                return render_template('forgot_password_sent.html', email=email)
        return render_template('forgot_password.html', error='Email not found')
    return render_template('forgot_password.html')

@app.route('/chat')
def chat():
    username = request.args.get('username')
    return render_template('chat.html', username=username)

@app.route('/send', methods=['POST'])
def send_message():
    data = request.get_json()
    user_input = data.get('message', '')
    if not user_input:
        return jsonify({'error': 'Empty message'}), 400
    try:
        response = chatbot.send_message(user_input)
        return jsonify({'response': response.text})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run()