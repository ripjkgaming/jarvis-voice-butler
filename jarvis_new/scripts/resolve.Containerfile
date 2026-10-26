FROM docker.io/library/ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    libasound2 libasound2-plugins pulseaudio-utils libatomic1 libfuse2 libgl1 libglu1-mesa libopengl0 libglib2.0-0 libgomp1 \
    libice6 libnss3 libsm6 libx11-6 libx11-xcb1 libxcb1 libxcb-cursor0 \
    libxcb-dri3-0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
    libxcb-render-util0 libxcb-render0 libxcb-shape0 libxcb-shm0 libxcb-sync1 \
    libxcb-xfixes0 libxcb-xinerama0 libxcb-xkb1 libxcb-xinput0 libc++1 libc++abi1 libxcomposite1 libxcursor1 \
    libxdamage1 libxext6 libxfixes3 libxi6 libxkbcommon-x11-0 libxrandr2 libxxf86vm1 \
    libxrender1 libxss1 libxtst6 ocl-icd-libopencl1 xkb-data \
    libapr1 libaprutil1 libfontconfig1 libfreetype6 libvulkan1 libxkbcommon0 \
    libdbus-1-3 libpulse0 libsndio7.0 libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 \
    libudev1 libuuid1 liblzma5 libcrypt1 libbz2-1.0 fonts-dejavu-core mesa-utils x11-utils x11-apps && \
    rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 --shell /bin/bash resolve
ENV HOME=/home/resolve QT_QPA_PLATFORM=xcb QT_XCB_GL_INTEGRATION=glx __GLX_VENDOR_LIBRARY_NAME=nvidia
USER resolve
ENTRYPOINT ["/opt/resolve/bin/resolve"]
