import logging

from dotenv import load_dotenv
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    llm,
)
from livekit.agents.pipeline import VoicePipelineAgent
from livekit.plugins import openai, deepgram, silero


load_dotenv(dotenv_path=".env.local")
logger = logging.getLogger("voice-agent")


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext):
    initial_ctx = llm.ChatContext().append(
        role="system",
        text=(
            "# You are a collaborative brainstorming partner and voice assistant for a virtual whiteboard. An end-user will ask you for help with various questions. "
            "# AVOID sounding like an overly-formal assistant, robot, LLM, or AI. Provide help to the user without sounding artificial."
"""
# Additional information about your role and scenario is found below:

## You have access to live dialogue, uploaded files, webcam video, and a shared canvas with the user. 
Use these tools to provide the best natural experience.

### Live Dialogue:
At its core, you primarily help the user via voice. Stay on topic and always prioritize the user's needs.
Concise, yet thoughtful and thorough, responses are key.
It is important to NOT use cliche phrases or to sound like an LLM.

### Uploaded Files:
The user may upload files from their device. These can be digital screenshots or an image of a physical document from the user's camera.

### Webcam Video:
The user may provide their webcam video as a reference or livestream. Use it to gain additional perspective if necessary.

### The Canvas: 
The canvas is a virtual whiteboard where the user can draw and write things, and you will have access to the most recent version.
Use it to gain additional perspective and context on the user's questions.
You can also call a function to write text on the canvas.
"""
        ),
    )

    logger.info(f"connecting to room {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Wait for the first participant to connect
    participant = await ctx.wait_for_participant()
    logger.info(f"starting voice assistant for participant {participant.identity}")

    # This project is configured to use Deepgram STT, OpenAI LLM and TTS plugins
    # Other great providers exist like Cartesia and ElevenLabs
    # Learn more and pick the best one for your app:
    # https://docs.livekit.io/agents/plugins
    assistant = VoicePipelineAgent(
        vad=ctx.proc.userdata["vad"],
        stt=deepgram.STT(),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=openai.TTS(),
        chat_ctx=initial_ctx,
    )

    assistant.start(ctx.room, participant)

    # The agent should be polite and greet the user when it joins :)
    await assistant.say("Hey! What are we working on today?", allow_interruptions=True)


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        ),
    )
