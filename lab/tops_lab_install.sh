#!/bin/bash

SERVICE_NAME="tops-lab"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
DOCKER_IMAGE="ghcr.io/f5xc-tenantops/f5xc-udf-lab-services/tops-lab:latest"
S3_BUCKET="f5xc-tops-registry"

# Ensure script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (use sudo)."
    exit 1
fi

echo "Installing or updating $SERVICE_NAME..."

# Install dependencies (Docker if not installed)
if ! command -v docker &> /dev/null; then
    echo "Docker not found. Installing..."
    apt-get update
    apt-get install -y docker.io
    systemctl enable --now docker
fi

# Stop and remove any existing container
if docker ps -q --filter "name=$SERVICE_NAME" | grep -q .; then
    echo "Stopping existing container..."
    docker stop $SERVICE_NAME
    docker rm -f $SERVICE_NAME
fi

# Remove old systemd service if it exists
if [ -f "$SERVICE_FILE" ]; then
    echo "Removing existing systemd service..."
    systemctl stop $SERVICE_NAME
    systemctl disable $SERVICE_NAME
    rm -f "$SERVICE_FILE"
fi

# Pull latest Docker image
echo "Pulling latest Docker image: $DOCKER_IMAGE..."
docker pull $DOCKER_IMAGE

# Create systemd service file
echo "Creating new systemd service..."
cat <<EOF > $SERVICE_FILE
[Unit]
Description=Tops Lab Metadata Service
Requires=docker.service
After=docker.service

[Service]
Restart=always
ExecStartPre=-/usr/bin/docker pull $DOCKER_IMAGE
ExecStart=/usr/bin/docker run --rm \\
    --name $SERVICE_NAME \\
    --env LAB_INFO_BUCKET=$S3_BUCKET \\
    -v /state:/state \\
    $DOCKER_IMAGE
ExecStop=/usr/bin/docker stop $SERVICE_NAME
ExecStopPost=/usr/bin/docker rm -f $SERVICE_NAME

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd, enable, and start service
echo "Enabling and starting $SERVICE_NAME service..."
systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl start $SERVICE_NAME

echo "Installation complete. $SERVICE_NAME is now running."