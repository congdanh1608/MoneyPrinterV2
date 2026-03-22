#!/bin/bash
# Quick start: VoiceBox server + MoneyPrinterV2

VOICEBOX_DIR="/Volumes/DEV/Projects/MySelf/voicebox"
MPV2_DIR="/Volumes/DEV/Projects/MySelf/MoneyPrinterV2"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== Starting VoiceBox Server ===${NC}"
cd "$VOICEBOX_DIR"
source backend/venv/bin/activate
python3 -m uvicorn backend.main:app --port 17493 &
VOICEBOX_PID=$!
echo -e "${YELLOW}VoiceBox PID: $VOICEBOX_PID${NC}"

echo "Waiting for VoiceBox..."
for i in {1..30}; do
    if curl -s http://127.0.0.1:17493/health > /dev/null 2>&1; then
        echo -e "${GREEN}VoiceBox ready!${NC}"
        break
    fi
    sleep 1
done

echo ""
echo -e "${GREEN}=== Starting MoneyPrinterV2 ===${NC}"
cd "$MPV2_DIR"
source venv/bin/activate
python3 src/main.py

echo -e "${YELLOW}Stopping VoiceBox (PID: $VOICEBOX_PID)...${NC}"
kill $VOICEBOX_PID 2>/dev/null
echo -e "${GREEN}Done!${NC}"
