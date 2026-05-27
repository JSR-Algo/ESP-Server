<template>
  <div class="audio-container">
    <audio
      ref="audioRef"
      :src="audioUrl"
      @timeupdate="updateProgress"
      @loadedmetadata="updateDuration"
      style="display: none"
    ></audio>

    <div class="custom-controls">
      <!-- Play/Pause button -->
      <button class="play-btn" @click="togglePlay">
        <svg width="20" height="20" viewBox="0 0 20 20">
          <path
            fill="#4362b3"
            :d="isPlaying
              ? 'M6 3h3v14H6zm5 0h3v14h-3z'
              : 'M5 3l12 7-12 7z'"
          />
        </svg>
      </button>

      <!-- Time display -->
      <span class="time-display">{{ formattedCurrentTime }}/{{ formattedDuration }}</span>

      <!-- Progress bar -->
      <div class="progress-container">
        <div class="progress-bar" @click="handleProgressClick">
          <div
            class="progress-track"
            :style="{ width: progress + '%' }"
          ></div>
          <div
            class="progress-thumb"
            :style="{ left: progress + '%' }"
            @mousedown="startDrag"
          ></div>
        </div>
      </div>

      <!-- Volume control -->
      <div class="volume-control" ref="volumeControl">
        <button
          @click="toggleMute"
          @mouseenter="handleVolumeMouseEnter"
          @mouseleave="startVolumeSliderHideTimer"
          ref="volumeButton"
          class="volume-button"
        >
          <svg width="20" height="20" viewBox="0 0 24 24">
            <path fill="currentColor" :d="volumeIconPath"/>
          </svg>
        </button>
        <div
          v-show="showVolumeSlider"
          class="volume-slider-container"
          @mouseenter="cancelVolumeSliderHideTimer"
          @mouseleave="startVolumeSliderHideTimer"
          ref="volumeSlider"
        >
          <div class="volume-slider-track">
            <input
              type="range"
              v-model="volume"
              min="0"
              max="1"
              step="0.1"
              class="volume-slider"
              @input="handleVolumeChange"
              orient="vertical"
            >
            <div class="volume-slider-thumb" :style="{ bottom: volume * 100 + '%' }"></div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'

const props = defineProps({
  audioUrl: String
})

// Audio element ref
const audioRef = ref(null)
const volumeButton = ref(null)
const volumeSlider = ref(null)

// PlayStatus
const isPlaying = ref(false)
const currentTime = ref(0)
const duration = ref(0)
const progress = ref(0)

// VolumeStatus
const volume = ref(0.7)
const isMuted = ref(false)
const showVolumeSlider = ref(false)
let volumeSliderHideTimer = null

// Format time as MM:SS
const formatTime = (seconds) => {
  const sec = Math.floor(seconds || 0)
  return `${Math.floor(sec / 60)}:${(sec % 60).toString().padStart(2, '0')}`
}

const formattedCurrentTime = computed(() => formatTime(currentTime.value))
const formattedDuration = computed(() => formatTime(duration.value))

// By volumeStatusReturn corresponding icon path
const volumeIconPath = computed(() => {
  if (isMuted.value || volume.value === 0) {
    return 'M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z'
  }
  return 'M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z'
})

// Playback control
const togglePlay = () => {
  if (isPlaying.value) {
    audioRef.value.pause()
  } else {
    audioRef.value.play()
  }
  isPlaying.value = !isPlaying.value
}

const updateDuration = () => {
  if (audioRef.value) {
    duration.value = audioRef.value.duration
  }
}

const updateProgress = () => {
  if (audioRef.value?.duration) {
    currentTime.value = audioRef.value.currentTime
    progress.value = (currentTime.value / duration.value) * 100
  }
}

const handleProgressClick = (e) => {
  const rect = e.currentTarget.getBoundingClientRect()
  const percentage = ((e.clientX - rect.left) / rect.width) * 100
  seekToPercentage(percentage)
}

// Volume control
const toggleMute = () => {
  isMuted.value = !isMuted.value
  audioRef.value.muted = isMuted.value

  // If unmuting and volume is0, restore default volume
  if (!isMuted.value && volume.value === 0) {
    volume.value = 0.5
    audioRef.value.volume = 0.5
  }
}

const handleVolumeChange = (e) => {
  const newVolume = parseFloat(e.target.value)
  audioRef.value.volume = newVolume
  isMuted.value = newVolume === 0
}

// Progress bar drag feature
let isDragging = false

const startDrag = (e) => {
  isDragging = true
  document.addEventListener('mousemove', handleDrag)
  document.addEventListener('mouseup', stopDrag)
  e.preventDefault()
}

const handleDrag = (e) => {
  if (!isDragging) return

  const rect = document.querySelector('.progress-bar').getBoundingClientRect()
  const percentage = ((e.clientX - rect.left) / rect.width) * 100
  seekToPercentage(percentage)
}

const stopDrag = () => {
  isDragging = false
  document.removeEventListener('mousemove', handleDrag)
  document.removeEventListener('mouseup', stopDrag)
}

const seekToPercentage = (percentage) => {
  const clampedPercentage = Math.min(Math.max(percentage, 0), 100)
  progress.value = clampedPercentage
  audioRef.value.currentTime = (clampedPercentage / 100) * duration.value
}

// Volume slider display/Hide control
const startVolumeSliderHideTimer = () => {
  volumeSliderHideTimer = setTimeout(() => {
    showVolumeSlider.value = false
  }, 300)
}

const cancelVolumeSliderHideTimer = () => {
  clearTimeout(volumeSliderHideTimer)
}

// Volume slider positioning
const updateSliderPosition = () => {
  nextTick(() => {
    if (!volumeButton.value || !volumeSlider.value) return

    const buttonRect = volumeButton.value.getBoundingClientRect()
    const slider = volumeSlider.value

    // Calculate position relative to viewport
    const left = buttonRect.left + window.scrollX
    const top = buttonRect.top + window.scrollY

    // Position at button center, vertically centered
    slider.style.left = `${left + 5}px`
    slider.style.top = `${top - 85}px`
  })
}

const handleVolumeMouseEnter = () => {
  showVolumeSlider.value = true
  updateSliderPosition()
}

// Initialize
onMounted(() => {
  if (audioRef.value) {
    audioRef.value.volume = volume.value
  }
  window.addEventListener('resize', updateSliderPosition)
})

onUnmounted(() => {
  window.removeEventListener('resize', updateSliderPosition)
})
</script>

<style scoped>
.audio-container {
  background: #eef0fd;
  padding: 8px;
  height: 40px;
  display: flex;
  align-items: center;
  border-radius: 5px;
}

.custom-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}

.play-btn {
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  display: flex;
  align-items: center;
}

.time-display {
  font-size: 12px;
  color: #5f7ba7;
  min-width: 40px;
  text-align: center;
}

.progress-container {
  flex: 1;
  padding: 0 10px;
  opacity: 0.7;
  transition: opacity 0.3s ease;
}

.progress-container:hover {
  opacity: 1;
}

.progress-bar {
  height: 2px;
  background: #bfcadb;
  position: relative;
  cursor: pointer;
}

.progress-track {
  position: absolute;
  height: 100%;
  background: #4167ed;
}

.progress-thumb {
  position: absolute;
  width: 12px;
  height: 12px;
  background: #4167ed;
  border: 2px solid #d6dcfc;
  border-radius: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  cursor: pointer;
  z-index: 2;
}

.volume-control {
  position: relative;
}

.volume-button {
  background: none;
  border: none;
  padding: 0;
  color: #8f95cd;
  cursor: pointer;
}

.volume-slider-container {
  position: fixed;
  padding: 10px 4px;
  background: #eef0fd;
  border-radius: 4px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  z-index: 1000;
  height: 75px;
}

.volume-slider-track {
  position: relative;
  height: 55px;
  width: 2px;
  background: #bfcadb;
  margin: 1px;
}

.volume-slider-thumb {
  position: absolute;
  left: 1px;
  width: 9px;
  height: 9px;
  background: #4167ed;
  border: 2px solid #d6dcfc;
  border-radius: 50%;
  transform: translateX(-50%);
  pointer-events: none;
}

.volume-slider {
  position: absolute;
  left: -14px;
  width: 30px;
  height: 60px;
  writing-mode: vertical-lr;
  direction: rtl;
  opacity: 0;
  cursor: pointer;
  z-index: 2;
}

.volume-slider-track::after {
  content: '';
  position: absolute;
  left: 0;
  bottom: 0;
  width: 2px;
  height: calc(100% * v-bind(volume));
  background: #4167ed;
}
</style>