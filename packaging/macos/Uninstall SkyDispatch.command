#!/bin/bash
# Removes SkyDispatch from this Mac. Double-click to run.
clear
echo "SkyDispatch uninstaller"
echo "-----------------------"
osascript -e 'tell application "SkyDispatch" to quit' >/dev/null 2>&1
sleep 1
if [ -d "/Applications/SkyDispatch.app" ]; then
  rm -rf "/Applications/SkyDispatch.app" 2>/dev/null || sudo rm -rf "/Applications/SkyDispatch.app"
  echo "Removed /Applications/SkyDispatch.app"
else
  echo "SkyDispatch.app was not found in /Applications."
fi
sudo rm -rf "/Library/Application Support/SkyDispatch" 2>/dev/null
sudo pkgutil --forget com.skydispatch.app >/dev/null 2>&1
echo
read -r -p "Also delete your career data, settings and downloaded voices? [y/N] " answer
if [[ "$answer" =~ ^[Yy]$ ]]; then
  rm -rf "$HOME/Library/Application Support/SkyDispatch" "$HOME/Library/Logs/SkyDispatch"
  echo "Career data deleted."
else
  echo "Your career data was kept in ~/Library/Application Support/SkyDispatch"
fi
echo
echo "Done. You can close this window."
