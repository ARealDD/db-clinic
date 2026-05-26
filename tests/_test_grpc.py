"""Quick gRPC connectivity test for db-clinic adapter debugging."""
import sys, os, threading, queue, grpc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python", "generated"))
import agent_pb2
import agent_pb2_grpc

GRPC_ADDR = "localhost:50051"
channel = grpc.insecure_channel(GRPC_ADDR)
stub = agent_pb2_grpc.AgentServiceStub(channel)

# Create session
resp = stub.CreateSession(agent_pb2.CreateSessionRequest(
    model="",
    system_prompts=["You are a database diagnosis assistant."],
))
session_id = resp.session_id
print(f"Session created: {session_id}")

# Build input iterator
input_q = queue.Queue()
stop_evt = threading.Event()

chat_input = agent_pb2.ChatInput(session_id=session_id)
chat_input.user_message.CopyFrom(agent_pb2.UserMessage(content="Hello, what is the root cause of slow queries?"))
input_q.put(chat_input)
input_q.put(("_WAIT", stop_evt))

class InputIter:
    def __init__(self, q):
        self._q = q
    def __iter__(self):
        return self
    def __next__(self):
        item = self._q.get()
        if item is None:
            raise StopIteration
        if isinstance(item, tuple) and item[0] == "_WAIT":
            if not item[1].wait(timeout=30):
                print("TIMEOUT waiting for stop event")
                raise StopIteration
            raise StopIteration
        return item

# Read responses
out_q = queue.Queue()
errors = []

def _read():
    try:
        for out in stub.Chat(iter(InputIter(input_q))):
            out_q.put(out)
        out_q.put(None)
    except Exception as e:
        errors.append(e)
        out_q.put(None)

reader = threading.Thread(target=_read, daemon=True)
reader.start()

print("Reading responses...")
count = 0
while True:
    out = out_q.get()
    if out is None:
        break
    count += 1
    field = out.WhichOneof("payload")
    if field == "text_delta":
        print(f"  [{count}] text_delta: '{out.text_delta.content[:80]}'")
    elif field == "turn_complete":
        print(f"  [{count}] turn_complete: stop_reason={out.turn_complete.stop_reason}, messages={len(out.turn_complete.messages)}")
        for msg in out.turn_complete.messages:
            print(f"    message role={msg.role}, blocks={len(msg.blocks)}")
            for block in msg.blocks:
                bf = block.WhichOneof("content")
                if bf == "text":
                    print(f"      text='{block.text[:80]}'")
    elif field == "tool_execution":
        print(f"  [{count}] tool_execution: {out.tool_execution.tool_name}")
    elif field == "error":
        print(f"  [{count}] error: {out.error.message}")
    else:
        print(f"  [{count}] {field}")

stop_evt.set()
reader.join()
channel.close()
print(f"\nTotal events: {count}, errors: {errors}")
