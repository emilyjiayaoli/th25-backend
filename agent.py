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

from livekit import rtc
from livekit.agents.llm import ChatMessage, ChatImage

import json
import asyncio
import aiohttp
from typing import Annotated
import os

load_dotenv(dotenv_path=".env.local")
logger = logging.getLogger("voice-agent")

## video
async def get_video_track(room: rtc.Room):
    """Find and return the first available remote video track in the room."""
    for participant_id, participant in room.remote_participants.items():
        for track_id, track_publication in participant.track_publications.items():
            if track_publication.track and isinstance(
                track_publication.track, rtc.RemoteVideoTrack
            ):
                logger.info(
                    f"Found video track {track_publication.track.sid} "
                    f"from participant {participant_id}"
                )
                return track_publication.track
    raise ValueError("No remote video track found in the room")


async def send_text_to_frontend(ctx: JobContext, text: str):
    # Create your message with a type (topic) and content.

    print("Sending text to frontend! in send_text_to_frontend")
    message = {
        "type": "whiteboard_update",
        "content": text,
    }
    data_str = json.dumps(message)
    data_bytes = data_str.encode("utf-8")

    await ctx.room.local_participant.publish_data(
        payload=data_bytes,
        reliable=True,
        topic="whiteboard_update"  # optional topic if needed
    )

# async def get_screen_share_track(room: rtc.Room):
#     """Find and return the first available remote screen share video track."""
#     for participant_id, participant in room.remote_participants.items():
#         for track_id, publication in participant.track_publications.items():
#             # Check that the track exists, is a video track, and its source indicates screen share.
#             if (
#                 publication.track 
#                 and isinstance(publication.track, rtc.RemoteVideoTrack)
#                 and publication.source == rtc.TrackSource.SCREEN_SHARE  # or "screen_share" depending on your SDK version
#             ):
#                 logger.info(
#                     f"Found screen share track {publication.track.sid} from participant {participant_id}"
#                 )
#                 return publication.track
#     raise ValueError("No remote screen share track found in the room")


async def get_latest_image(room: rtc.Room):
    print("get_latest_image!!")
    """Capture and return a single frame from the video track."""
    video_stream = None
    try:
        video_track = await get_video_track(room)
        video_stream = rtc.VideoStream(video_track)
        async for event in video_stream:
            logger.debug("Captured latest video frame")
            return event.frame
    except Exception as e:
        logger.error(f"Failed to get latest image: {e}")
        return None
    finally:
        if video_stream:
            await video_stream.aclose()

# video end

# async def send_text_to_whiteboard(ctx: JobContext, text: str):
#     """Send AI-generated text to whiteboard via LiveKit DataChannel."""
#     if ctx.room and ctx.room.local_participant:
#         message = {"type": "whiteboard_update", "content": text}
#         await ctx.room.local_participant.publish_data(
#             data=str(message).encode("utf-8"),
#             kind=rtc.DataPacketKind.RELIABLE
#         )



def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext):

    async def before_llm_cb(assistant: VoicePipelineAgent, chat_ctx: llm.ChatContext):
        """
        Callback that runs right before the LLM generates a response.
        Captures the current video frame and adds it to the conversation context.
        """

        # truncate msg to latest 15 msgs to prevent ooms :)
        if len(chat_ctx.messages) > 15:
            chat_ctx.messages = chat_ctx.messages[-15:]


        latest_image = await get_latest_image(ctx.room)
        if latest_image:
            image_content = [ChatImage(image=latest_image)]
            chat_ctx.messages.append(ChatMessage(role="user", content=image_content))
            logger.debug("Added latest frame to conversation context")
            print("chat_ctx", chat_ctx)


    initial_ctx = llm.ChatContext().append(
    role="system",
    text=(
        "You are a voice assistant created by LiveKit that can both see (either a canvas or video) and hear. This is you're workspace"
        "You should use SHORT and concise responses unless student asks to explain, avoiding unpronounceable punctuation. "
        "When you see an image in our conversation, naturally incorporate what you see "
        "into your response. Keep visual descriptions brief but informative."
        "You are the best teacher. You can teach about anything. adapt to the student's learning style. Be able to break problems down."
    ),
)

    logger.info(f"connecting to room {ctx.room.name}")
    # await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)

    # Wait for the first participant to connect
    participant = await ctx.wait_for_participant()
    logger.info(f"starting voice assistant for participant {participant.identity}")

    # This project is configured to use Deepgram STT, OpenAI LLM and TTS plugins
    # Other great providers exist like Cartesia and ElevenLabs
    # Learn more and pick the best one for your app:
    # https://docs.livekit.io/agents/plugins
    fnc_ctx = AssistantFnc(ctx=ctx)


    assistant = VoicePipelineAgent(
        vad=ctx.proc.userdata["vad"],
        stt=deepgram.STT(),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=openai.TTS(),
        chat_ctx=initial_ctx,
        before_llm_cb=before_llm_cb,
        fnc_ctx=fnc_ctx,
    )

    assistant.start(ctx.room, participant)

    # @assistant.on("agent_speech_committed")
    # def agent_speech_committed(msg: llm.ChatMessage):
    #     # Extract text from the message (ensure it's a string)
    #     text = msg.content if isinstance(msg.content, str) else str(msg.content)
    #     print(f"Agent response committed: {text}")
    #     # Create an async task to send the text to the frontend
    #     asyncio.create_task(send_text_to_frontend(ctx, text))
    #     print(f"Agent response committedafter: {text}")

    # @assistant.on("agent_started_speaking")
    # def agent_started_speaking(response_text: str):
    #     """This function is triggered every time the assistant starts speaking.
    #     It schedules the asynchronous sending of the AI response to the frontend.
    #     """
    #     print(f"agent_started_speaking. Sending AI response to frontend: {response_text}")
    #     # Schedule the async task; don't await it here since the callback must be synchronous.
    #     asyncio.create_task(send_text_to_frontend(ctx, response_text))

    # print("ctx", assistant.chat_ctx.messages[-1])
    # await send_text_to_frontend(ctx, str(assistant.chat_ctx.messages[-1]))

    # # The agent should be polite and greet the user when it joins :)
    # await assistant.say("Hey, what would you like to learn today?", allow_interruptions=True)


    
    
    greeting_text = "Hey, what would you like to learn today?"

    await assistant.say(greeting_text, allow_interruptions=True),
    await send_text_to_frontend(ctx, greeting_text)

    # # Hook into the assistant's chat pipeline to send responses in real time
    # @assistant.on("agent_started_speaking")
    # async def on_response(response_text: str):
    #     """This function is triggered every time the assistant generates a response"""
    #     print(f"agent_started_speaking. Sending AI response to frontend: {response_text}")
    #     await send_text_to_frontend(ctx, response_text)

    # # Attach the callback to process assistant responses
    # # assistant.llm.on_response = on_response
    
    # @assistant.on("agent_started_speaking")
    # def on_response(response_text: str):
    #     """This function is triggered every time the assistant starts speaking.
    #     It schedules the asynchronous sending of the AI response to the frontend.
    #     """
    #     print(f"agent_started_speaking. Sending AI response to frontend: {response_text}")
    #     # Schedule the async task; don't await it here since the callback must be synchronous.
    #     asyncio.create_task(send_text_to_frontend(ctx, response_text))

# first define a class that inherits from llm.FunctionContext
class AssistantFnc(llm.FunctionContext):
    def __init__(self, ctx: JobContext):
        self.ctx = ctx
        super().__init__()

    # the llm.ai_callable decorator marks this function as a tool available to the LLM
    # by default, it'll use the docstring as the function's description
    @llm.ai_callable()
    async def take_notes(
        self,
        notes: Annotated[
            str, llm.TypeInfo(description="Concise personalized notes that should be displayed on the frontend that student sees")
        ]
    ):
        """
        Called when the LLM instructs to take notes.
        This function triggers sending the notes to the frontend.
        """
        logger.info(f"Taking notes: {notes}")
        notes = "Notes: " + notes
        # Trigger sending the notes to the frontend.
        await send_text_to_frontend(self.ctx, notes)
        # Optionally, return a confirmation message to be included in the LLM's response.
        return f"Notes taken: {notes}"
    

    # @llm.ai_callable()
    # async def search_text(
    #     self,
    #     query: Annotated[
    #         str, llm.TypeInfo(description="Semantically search what the user uploaded")
    #     ]
    # ):
    #     """
    #     Search for relevant files based on the given query.

    #     This endpoint sends a GET request to the local search service and returns a structured JSON response.
        
    #     Expected response format:
    #     {
    #     "query": "What is the capital of France?",
    #     "answer": "Paris",
    #     "relevantFiles": [
    #         {
    #         "filename": "file1.txt",
    #         "matchReason": "Contains the word 'Paris'",
    #         "score": 0.8
    #         },
    #         {
    #         "filename": "file2.pdf",
    #         "matchReason": "Contains the phrase 'capital city of France'",
    #         "score": 0.6
    #         }
    #     ]
    #     }
    #     """
    #     logger.info(f"Calling search on query: {query}")
    #     url = "http://127.0.0.1:5000/search"  # URL of your local search endpoint

    #     # Create a client session to perform the HTTP GET request with the query parameter
    #     async with aiohttp.ClientSession() as session:
    #         async with session.get(url, params={"query": query}) as response:
    #             if response.status == 200:
    #                 # Assuming the search service returns JSON in the desired format
    #                 result = await response.json()
    #                 logger.info("Search successful")
    #                 if "answer" in result:
    #                     logger.log(f"Recorded the following answer {result["answer"]}")
    #                     return result["answer"]
    #                 else:
    #                     return ""
    #             else:
    #                 error_text = await response.text()
    #                 logger.error(f"Search request failed with status {response.status}: {error_text}")
    #                 # Optionally, you can raise an error here or return a structured error response
    #                 return {"error": f"Search request failed: {error_text}"}
            
        
    @llm.ai_callable()
    async def get_weather(
        self,
        # by using the Annotated type, arg description and type are available to the LLM
        location: Annotated[
            str, llm.TypeInfo(description="The location to get the weather for")
        ],
    ):
        """Called when the user asks about the weather. This function will return the weather for the given location."""
        logger.info(f"getting weather for {location}")
        url = f"https://wttr.in/{location}?format=%C+%t"
        logger.info(f"fetching weather data from {url}")
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    weather_data = await response.text()
                    # response from the function call is returned to the LLM
                    # as a tool response. The LLM's response will include this data
                    return f"The weather in {location} is {weather_data}."
                else:
                    raise f"Failed to get weather data, status code: {response.status}"

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        ),
    )
