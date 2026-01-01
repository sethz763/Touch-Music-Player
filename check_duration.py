import av
path = 'C:/Users/Seth Zwiebel/Music/Ball and Chain - Cage The Elephant.mp3'
container = av.open(path)
stream = next((s for s in container.streams if s.type == "audio"), None)
if stream:
    duration = float(stream.duration * stream.time_base)
    print(f"File duration: {duration:.2f}s ({duration/60:.2f} min)")
    print(f"Stream duration: {stream.duration}, time_base: {stream.time_base}")
    sr = stream.rate
    print(f"Sample rate: {sr}, Frames in file: {int(duration * sr)}")
else:
    print("No audio stream found")
container.close()
