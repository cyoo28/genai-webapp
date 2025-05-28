from flask import Flask, render_template, request, jsonify
from google import genai
from google.genai.types import GenerateContentConfig

"""
import datetime

from google import genai
from google.genai.types import (
    CreateBatchJobConfig,
    CreateCachedContentConfig,
    EmbedContentConfig,
    FunctionDeclaration,
    GenerateContentConfig,
    Part,
    SafetySetting,
    Tool,
)
"""

# Set up google api
PROJECT_ID = "symphony-gce-dev-secops"
LOCATION = "us-east4"
MODEL_ID = "gemini-2.0-flash-001"
client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)

system_instruction = """
  You are a helpful language translator.
  Your mission is to translate text in English to French.
"""

chat = client.chats.create(
    model=MODEL_ID,
    history=[],
    config=GenerateContentConfig(
       system_instruction=system_instruction,
       temperature=0.4,
        top_p=0.95,
        top_k=20,
        candidate_count=1,
        seed=5,
        max_output_tokens=100,
        stop_sequences=["STOP!"],
        presence_penalty=0.0,
        frequency_penalty=0.0
       )
    )

user_followup = "What happens next?"
response_stream_followup = chat.send_message_stream(user_followup)
print("User:", user_followup)
print("Model (streaming):")
for chunk in response_stream_followup:
    print(chunk.text, end="", flush=True)
print()

"""
def get_completion(prompt):
    print(prompt)
    query = openai.Completion.create(
        engine="text-davinci-003",
        prompt=prompt,
        max_tokens=1024,
        n=1,
        stop=None,
        temperature=0.5,
    )

    response = query.choices[0].text
    return response
"""
# set up flask webapp
app = Flask(__name__)

@app.route('/hello')
def hello_world():
    return 'Hello World'

"""
@app.route("/", methods=['POST', 'GET'])
def query_view():
    if request.method == 'POST':
        print('step1')
        prompt = request.form['prompt']
        response = get_completion(prompt)
        print(response)

        return jsonify({'response': response})
    return render_template('index.html')
"""

if __name__ == '__main__':
   app.run()