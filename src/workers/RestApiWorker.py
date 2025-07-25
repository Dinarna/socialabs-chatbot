from email.mime import message
from multiprocessing.connection import Connection
from flask import Flask, request, jsonify
from flask_classful import FlaskView, route
import threading
import uuid
import asyncio
import time
import utils.log as log
from utils.handleMessage import sendMessage, convertMessage
from .Worker import Worker

app = Flask(__name__)

class RestApiWorker(FlaskView, Worker):
    ###############
    # dont edit this part
    ###############
    route_base = "/"
    conn:Connection
    requests: dict = {}
    def __init__(self):
        # we'll assign these in run()
        self._port: int = None

        self.requests: dict = {}
        
    def run(self, conn: Connection, port: int):
        # assign here
        RestApiWorker.conn = conn
        self._port = port

        RestApiWorker.register(app)

        # start background threads *before* blocking server
        threading.Thread(target=self.listen_task, daemon=True).start()
        threading.Thread(target=self.health_check, daemon=True).start()

        app.run(debug=True, port=self._port, use_reloader=False)
        # asyncio.run(self.listen_task())
        self.health_check()


    def health_check(self):
        """Send a heartbeat every 10s."""
        while True:
            sendMessage(
                conn=RestApiWorker.conn,
                messageId="heartbeat",
                status="healthy"
            )
            time.sleep(10)
    def listen_task(self):
        while True:
            try:
                if RestApiWorker.conn.poll(1):  # Check for messages with 1 second timeout
                    raw = RestApiWorker.conn.recv()
                    msg = convertMessage(raw)
                    self.onProcessed(raw)
            except EOFError:
                break
            except Exception as e:
                log(f"Listener error: {e}",'error' )
                break

    def onProcessed(self, msg: dict):
        """
        Called when a worker response comes in.
        msg must contain 'messageId' and 'data'.
        """
        task_id = msg.get("messageId")
        entry = RestApiWorker.requests[task_id]
        if not entry:
            return
        entry["response"] = msg.get("data")
        entry["event"].set()
    def sendToOtherWorker(self, destination: str, data):
      task_id = str(uuid.uuid4())
      evt = threading.Event()
      
      RestApiWorker.requests[task_id] = {
          "event": evt,
          "response": None
      }
      print(f"Sending request to {destination} with task_id: {task_id}")
      
      sendMessage(
          conn=RestApiWorker.conn,
          messageId=task_id,
          status="processing",
          destination=destination,
          data=data
      )
      if not evt.wait(timeout=300):
          # timeout
          return {
              "taskId": task_id,
              "status": "timeout",
              "result": None
          }
      
      # success
      result = RestApiWorker.requests.pop(task_id)["response"]
      return {
          "taskId": task_id,
          "status": "completed",
          "result": result
      }

    ##########################################
    # FLASK ROUTES FUNCTIONS
    ##########################################
    @route('/', methods=['GET'])
    def getData(self):
      projectId = request.args.get('projectId')
      response = self.sendToOtherWorker(
          destination=[f"DatabaseInteractionWorker/getData/{projectId}"],
          data=projectId
      )
      if response["status"] == "timeout":
          return jsonify({"error": "Request timed out"}), 504
      elif response["status"] == "completed":
          return jsonify(response["result"]), 200
      else:
          return jsonify({"error": "Unknown error"}), 500

    @route('/test', methods=['POST'])
    def getData(self):
      
      projectId = request.json.get('projectId')
      prompt = request.json.get('prompt')
      
      message = self.sendToOtherWorker(
            destination=["DatabaseInteractionWorker/createNewHistory/"],
            data={
                "question": prompt,
                "projectId": projectId
            }
        )
      id = message.get("result", [{}])[0].get("_id", "unknown_id")
      
      response = self.sendToOtherWorker(
          destination=[f"CRAGWorker/generateAnswer/{id}"],
          data={
              "projectId": projectId,
              "prompt": prompt
          }
      )
      
      if response["status"] == "timeout":
          return jsonify({"error": "Request timed out"}), 504
      elif response["status"] == "completed":
          return jsonify(response["result"]), 200
      else:
          return jsonify({"error": "Unknown error"}), 500
    @route('/send-test', methods=['GET'])
    def sendTest(self):
        """
        A test route to send a message to another worker.
        """
        response = self.sendToOtherWorker(
            destination=["DatabaseInteractionWorker/createNewHistory/"],
            data={
                "question": "What is the capital of France?",
                "projectId": "12345"
            }
        )
        # print(response)
        id = response.get("result", [{}])[0].get("_id", "unknown_id")
        process_name = "test_process1"
        self.sendToOtherWorker(
            destination=[f"DatabaseInteractionWorker/createNewProgress/{id}"],
            data={
                "process_name": process_name,
                "input": "test_input1",
                "output": "test_output1",
            }
        )
        #    sub_process_name = data.get('sub_process_name', '')
    #   input = data.get('input', '')
    #   output = data.get('output', '')
        self.sendToOtherWorker(
            destination=[f"DatabaseInteractionWorker/updateProgress/{id}"],
            data={
                "process_name": process_name,
                "sub_process_name": "test_sub_process",
                "input": "test_sub_process-2",
                "output": "tes-2",
            }
        )
        if response["status"] == "timeout":
            return jsonify({"error": "Request timed out"}), 504
        elif response["status"] == "completed":
            return jsonify(response["result"]), 200
        else:
            return jsonify({"error": "Unknown error"}), 500

def main(conn: Connection, config: dict):
    
    worker = RestApiWorker()
    worker.run(conn, config.get("port", 5000))
