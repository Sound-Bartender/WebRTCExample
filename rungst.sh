gst-launch-1.0 libcamerasrc ! video/x-raw,width=640,height=480,framerate=30/1 \
     ! queue \
     ! x264enc tune=zerolatency bitrate=500 speed-preset=ultrafast key-int-max=10 \
     ! h264parse config-interval=-1 \
     ! rtph264pay config-interval=1 pt=96 \
     ! udpsink host=192.168.0.12 port=5000 sync=false
