import os
import subprocess
import requests

class VideoCompressor:
    def __init__(self, input_url, local_input="input.mp4", local_output="compressed_output.mp4"):
        self.input_url = input_url
        self.local_input = local_input
        self.local_output = local_output

    def download_video(self):
        """ Download video from a URL and save it locally """
        print(f"📥 Downloading video from: {self.input_url}")
        response = requests.get(self.input_url, stream=True)

        if response.status_code == 200:
            with open(self.local_input, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
            print(f"✅ Download complete: {self.local_input}")
        else:
            raise Exception(f"Failed to download video: {response.status_code}")

    def compress_video(self):
        """ Compress video using FFmpeg and save locally """
        print(f"⚙️ Compressing video: {self.local_input} → {self.local_output}")

        ffmpeg_command = [
            "ffmpeg", "-y",
            "-i", self.local_input,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "30",
            "-b:v", "1200k",
            "-c:a", "aac", "-b:a", "64k",
            "-threads", "8",
            self.local_output
        ]
        try:
            subprocess.run(ffmpeg_command, check=True)
            print(f"✅ Compression complete! Saved as: {self.local_output}")
        except subprocess.CalledProcessError as e:
            raise Exception(f"Error compressing video: {e}")

    def process(self):
        """ Full workflow: Download, Compress, and Save """
        self.download_video()
        self.compress_video()

# Example Usage:
if __name__ == "__main__":
    input_video_url = "https://quickcampaignvideos.s3.us-east-1.amazonaws.com/hero-video.mp4"

    compressor = VideoCompressor(input_video_url)
    compressor.process()
