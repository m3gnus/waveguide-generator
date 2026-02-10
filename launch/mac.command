#!/bin/bash
# Waveguide Generator launcher for macOS
# Double-click this file in Finder to start the app

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
npm start
