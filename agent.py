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
        latest_image = await get_latest_image(ctx.room)
        if latest_image:
            image_content = [ChatImage(image=latest_image)]
            chat_ctx.messages.append(ChatMessage(role="user", content=image_content))
            logger.debug("Added latest frame to conversation context")
            print("chat_ctx", chat_ctx)


    initial_ctx = llm.ChatContext().append(
    role="system",
    text=(
        "You are a voice assistant created by LiveKit that can both see and hear. "
        "You should use short and concise responses, avoiding unpronounceable punctuation. "
        "When you see an image in our conversation, naturally incorporate what you see "
        "into your response. Keep visual descriptions brief but informative."
        "You are the best teacher. You can teach about anything. adapt to the student's learning style. Be able to break problems down."
    ),
)

    # initial_ctx = llm.ChatContext().append(
    #     role="system",
    #     text=("""You are the best teacher. You can teach about anything. adapt to the student's learning style. Be able to break problems down."""
    #     ),
    # )

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

    assistant = VoicePipelineAgent(
        vad=ctx.proc.userdata["vad"],
        stt=deepgram.STT(),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=openai.TTS(),
        chat_ctx=initial_ctx,
        before_llm_cb=before_llm_cb
    )

    assistant.start(ctx.room, participant)

    await send_text_to_frontend(ctx, "hii im here")

    # The agent should be polite and greet the user when it joins :)
    await assistant.say("Hey, what would you like to learn today?", allow_interruptions=True)

    
    


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        ),
    )
