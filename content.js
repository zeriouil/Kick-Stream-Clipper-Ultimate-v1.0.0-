// Kick Stream Clipper Content Script
// Responsible for polling for the video element, injecting the UI, binding event controls, and interacting with the backend.

let activeVideo = null;
let activeVideoSrc = null;
let clipperContainer = null;
let startVal = 0;
let endVal = 0;
let currentMin = 0;
let currentMax = 100;
let pollIntervalId = null;
let timeUpdateListener = null;

// Tab state and format controls
let activeTab = 'editor'; // 'editor' or 'gallery'
let aspectMode = '16:9'; // '16:9', '9:16' or 'split_screen'
let cropOffsetPct = 50;

// Facecam Box coordinate state (percentages of video dimensions: 0-100)
let facecamX = 10;
let facecamY = 10;
let facecamW = 25;
let facecamH = 25;
let isDraggingFacecam = false;
let isResizingFacecam = false;
let dragStartX = 0;
let dragStartY = 0;
let initialFacecamX = 0;
let initialFacecamY = 0;
let initialFacecamW = 0;
let initialFacecamH = 0;

// Helper to format seconds into HH:MM:SS
function formatTime(seconds) {
  if (isNaN(seconds) || seconds === Infinity) return "00:00:00";
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  
  const hStr = hrs.toString().padStart(2, '0');
  const mStr = mins.toString().padStart(2, '0');
  const sStr = secs.toString().padStart(2, '0');
  
  return `${hStr}:${mStr}:${sStr}`;
}

// Find the highest logical player wrapper to place the clipping UI directly underneath
function getPlayerWrapper(video) {
  let current = video;
  while (current && current.parentElement) {
    const parent = current.parentElement;
    if (
      parent.tagName === 'BODY' || 
      parent.tagName === 'MAIN' || 
      parent.id === 'app' || 
      parent.classList.contains('layout-wrapper') ||
      parent.classList.contains('main-container')
    ) {
      return current;
    }
    if (
      parent.classList.contains('video-player') || 
      parent.id === 'video-player' || 
      parent.classList.contains('react-player') ||
      parent.querySelector('[data-player]')
    ) {
      return parent;
    }
    current = parent;
  }
  return video.parentElement;
}

// Injects the custom HTML overlay below the player
function injectClipper(video) {
  console.log('Injecting Kick Stream Clipper UI...');

  // Create UI Container
  clipperContainer = document.createElement('div');
  clipperContainer.id = 'kick-custom-clipper';

  clipperContainer.innerHTML = `
    <div class="clipper-header">
      <div class="clipper-title-group">
        <span class="clipper-badge-live">LIVE</span>
        <span class="clipper-title"><span class="neon-text">KICK</span> CLIPPER</span>
      </div>
      <div class="clipper-tabs">
        <button class="btn-tab active" id="tab-btn-editor">Clip Editor</button>
        <button class="btn-tab" id="tab-btn-gallery">Local Gallery</button>
      </div>
    </div>

    <div class="clipper-body">
      <!-- 1. Editor Tab Content -->
      <div id="clipper-tab-content-editor" class="tab-content">
        <!-- Visual timeline highlight track -->
        <div class="timeline-container">
          <span class="timeline-edge-label" id="timeline-start-label">00:00:00</span>
          <div class="timeline-visual">
            <div class="timeline-track-filled" id="timeline-highlight"></div>
            <div class="timeline-playhead" id="timeline-playhead"></div>
          </div>
          <span class="timeline-edge-label" id="timeline-end-label">00:00:00</span>
        </div>

        <!-- Double sliders -->
        <div class="sliders-container">
          <div class="slider-row">
            <div class="slider-label-group">
              <label for="clipper-start-slider">Start Offset</label>
              <span class="slider-time" id="start-time-display">00:00:00</span>
            </div>
            <div class="slider-control">
              <input type="range" id="clipper-start-slider" min="0" max="100" value="0" step="0.1">
            </div>
          </div>
          
          <div class="slider-row">
            <div class="slider-label-group">
              <label for="clipper-end-slider">End Offset</label>
              <span class="slider-time" id="end-time-display">00:00:00</span>
            </div>
            <div class="slider-control">
              <input type="range" id="clipper-end-slider" min="0" max="100" value="100" step="0.1">
            </div>
          </div>
        </div>

        <!-- Precision inputs and "Set Current" buttons -->
        <div class="inputs-container">
          <div class="input-group">
            <label>Start Secs</label>
            <div class="input-wrapper">
              <input type="number" id="clipper-start-input" min="0" step="1" value="0">
              <button class="btn-secondary" id="btn-set-start-current" title="Set to current stream time">Set Current</button>
            </div>
          </div>

          <div class="input-group">
            <label>End Secs</label>
            <div class="input-wrapper">
              <input type="number" id="clipper-end-input" min="0" step="1" value="0">
              <button class="btn-secondary" id="btn-set-end-current" title="Set to current stream time">Set Current</button>
            </div>
          </div>
        </div>

        <!-- Formatting Grid (Aspect Ratio, Watermark, Volume, Scaling) -->
        <div class="settings-grid">
          <div class="settings-column">
            <!-- Layout Selector -->
            <div class="setting-row">
              <label for="clipper-layout-mode">Layout Mode</label>
              <select id="clipper-layout-mode" class="select-primary">
                <option value="16:9">Widescreen (16:9)</option>
                <option value="9:16">Vertical Crop (9:16)</option>
                <option value="split_screen">Split-Screen (9:16)</option>
              </select>
            </div>
            
            <!-- Crop position (visible for vertical / split-screen modes) -->
            <div class="setting-row hidden" id="crop-slider-row">
              <div class="slider-label-group">
                <label for="clipper-crop-slider">Gameplay Center</label>
                <span class="slider-time" id="crop-offset-display">50% (Center)</span>
              </div>
              <div class="slider-control">
                <input type="range" id="clipper-crop-slider" min="0" max="100" value="50" step="1">
              </div>
            </div>

            <!-- Audio Volume Boost -->
            <div class="setting-row">
              <div class="slider-label-group">
                <label for="clipper-volume-slider">Volume Level</label>
                <span class="slider-time" id="volume-level-display">100% (Normal)</span>
              </div>
              <div class="slider-control">
                <input type="range" id="clipper-volume-slider" min="0" max="300" value="100" step="5">
              </div>
            </div>
          </div>

          <div class="settings-column">
            <!-- Watermark text -->
            <div class="setting-row">
              <label for="clipper-watermark-text">Watermark Text</label>
              <input type="text" id="clipper-watermark-text" class="input-text-primary" placeholder="e.g. @streamer">
            </div>

            <!-- Watermark position -->
            <div class="setting-row">
              <label for="clipper-watermark-pos">Watermark Position</label>
              <select id="clipper-watermark-pos" class="select-primary">
                <option value="top_right">Top Right</option>
                <option value="top_left">Top Left</option>
                <option value="bottom_center">Bottom Center</option>
              </select>
            </div>

            <!-- Resolution selector -->
            <div class="setting-row">
              <label for="clipper-resolution-select">Output Resolution</label>
              <select id="clipper-resolution-select" class="select-primary">
                <option value="source">Source Quality</option>
                <option value="1080">1080p Full HD</option>
                <option value="720">720p HD</option>
                <option value="480">480p SD</option>
              </select>
            </div>
          </div>
        </div>

        <!-- Action Panel -->
        <div class="actions-container">
          <div class="clip-info-badge">
            Selected Length: <span id="clip-duration-display" class="neon-text">0.00s</span>
          </div>
          <div class="action-btn-group">
            <div class="clipper-status" id="clipper-status-badge">
              <span class="status-dot"></span>
              <span id="clipper-status-text">Scanning Stream Playlist...</span>
            </div>
            <button id="btn-generate-clip" class="btn-primary">
              <span class="btn-text">Generate Clip (.mp4)</span>
              <div class="btn-spinner hidden" id="clipper-btn-spinner"></div>
            </button>
          </div>
        </div>
      </div>

      <!-- 2. Gallery Tab Content -->
      <div id="clipper-tab-content-gallery" class="tab-content hidden">
        <!-- Generated clips will be loaded here dynamically -->
      </div>

      <!-- Notification overlay inside control panel -->
      <div id="clipper-notification" class="clipper-notification hidden"></div>
    </div>
  `;

  const playerWrapper = getPlayerWrapper(video);
  if (playerWrapper && playerWrapper.parentNode) {
    playerWrapper.parentNode.insertBefore(clipperContainer, playerWrapper.nextSibling);
    console.log('Successfully appended clipper panel below player wrapper.');
  } else {
    video.parentNode.appendChild(clipperContainer);
  }

  setupEventListeners(video);
  queryStreamUrl();
}

// Queries the background script to verify if the stream URL has been captured
function queryStreamUrl() {
  chrome.runtime.sendMessage({ action: 'get_stream_url' }, (response) => {
    const badge = document.getElementById('clipper-status-badge');
    const text = document.getElementById('clipper-status-text');
    
    if (!badge || !text) return;

    if (response && response.streamUrl) {
      badge.classList.add('status-active');
      text.textContent = 'Playlist Captured';
      console.log('Stream playlist confirmed by background script:', response.streamUrl);
    } else {
      badge.classList.remove('status-active');
      text.textContent = 'Scanning...';
    }
  });
}

// Update playhead positioning on the visual timeline
function updatePlayhead(video) {
  const playhead = document.getElementById('timeline-playhead');
  if (!playhead) return;
  const totalRange = currentMax - currentMin;
  if (totalRange > 0) {
    const playPct = ((video.currentTime - currentMin) / totalRange) * 100;
    playhead.style.left = `${Math.max(0, Math.min(100, playPct))}%`;
  } else {
    playhead.style.left = '0%';
  }
}

// Drag & Resize script for the facecam crop overlay
function setupFacecamDragAndResize(video) {
  const frame = document.getElementById('kick-clipper-facecam-box');
  const resizer = document.getElementById('facecam-resizer');
  
  if (!frame || !resizer) return;

  // Drag handler
  const onMouseDown = (e) => {
    if (e.target === resizer) return; 
    isDraggingFacecam = true;
    dragStartX = e.clientX;
    dragStartY = e.clientY;
    initialFacecamX = facecamX;
    initialFacecamY = facecamY;
    
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
    e.preventDefault();
  };

  const onMouseMove = (e) => {
    if (!isDraggingFacecam) return;
    const videoRect = video.getBoundingClientRect();
    const deltaX = e.clientX - dragStartX;
    const deltaY = e.clientY - dragStartY;
    
    const deltaXPct = (deltaX / videoRect.width) * 100;
    const deltaYPct = (deltaY / videoRect.height) * 100;
    
    facecamX = Math.max(0, Math.min(100 - facecamW, initialFacecamX + deltaXPct));
    facecamY = Math.max(0, Math.min(100 - facecamH, initialFacecamY + deltaYPct));
    
    updateCropOverlay(video);
  };

  const onMouseUp = () => {
    isDraggingFacecam = false;
    document.removeEventListener('mousemove', onMouseMove);
    document.removeEventListener('mouseup', onMouseUp);
  };

  frame.addEventListener('mousedown', onMouseDown);

  // Resize handler
  const onResizeMouseDown = (e) => {
    isResizingFacecam = true;
    dragStartX = e.clientX;
    dragStartY = e.clientY;
    initialFacecamW = facecamW;
    initialFacecamH = facecamH;
    
    document.addEventListener('mousemove', onResizeMouseMove);
    document.addEventListener('mouseup', onResizeMouseUp);
    e.preventDefault();
    e.stopPropagation(); // Avoid triggering drag listener
  };

  const onResizeMouseMove = (e) => {
    if (!isResizingFacecam) return;
    const videoRect = video.getBoundingClientRect();
    const deltaX = e.clientX - dragStartX;
    const deltaY = e.clientY - dragStartY;
    
    const deltaWPct = (deltaX / videoRect.width) * 100;
    const deltaHPct = (deltaY / videoRect.height) * 100;
    
    facecamW = Math.max(10, Math.min(100 - facecamX, initialFacecamW + deltaWPct));
    facecamH = Math.max(10, Math.min(100 - facecamY, initialFacecamH + deltaHPct));
    
    updateCropOverlay(video);
  };

  const onResizeMouseUp = () => {
    isResizingFacecam = false;
    document.removeEventListener('mousemove', onResizeMouseMove);
    document.removeEventListener('mouseup', onResizeMouseUp);
  };

  resizer.addEventListener('mousedown', onResizeMouseDown);
}

// Visual TikTok crop box drawing and synchronization
function updateCropOverlay(video) {
  if (aspectMode !== '9:16' && aspectMode !== 'split_screen') {
    removeCropOverlay();
    return;
  }

  const playerWrapper = getPlayerWrapper(video);
  if (!playerWrapper) return;

  if (window.getComputedStyle(playerWrapper).position === 'static') {
    playerWrapper.style.position = 'relative';
  }

  let overlay = document.getElementById('kick-clipper-crop-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'kick-clipper-crop-overlay';
    overlay.innerHTML = `
      <div class="crop-mask-left" id="crop-mask-left"></div>
      <div class="crop-frame" id="crop-frame">
        <div class="crop-frame-corner top-left"></div>
        <div class="crop-frame-corner top-right"></div>
        <div class="crop-frame-corner bottom-left"></div>
        <div class="crop-frame-corner bottom-right"></div>
      </div>
      <div class="crop-mask-right" id="crop-mask-right"></div>
      <!-- Facecam Box overlay (for split-screen layout) -->
      <div class="facecam-crop-box hidden" id="kick-clipper-facecam-box">
        <div class="facecam-crop-header">FACECAM (DRAG/RESIZE)</div>
        <div class="facecam-crop-resizer" id="facecam-resizer"></div>
      </div>
    `;
    playerWrapper.appendChild(overlay);
    
    // Bind Drag & Resize mouse controls
    setupFacecamDragAndResize(video);
  }

  // Align overlay precisely with the video element's size and positioning
  const videoRect = video.getBoundingClientRect();
  const wrapperRect = playerWrapper.getBoundingClientRect();

  const top = videoRect.top - wrapperRect.top;
  const left = videoRect.left - wrapperRect.left;
  const width = videoRect.width;
  const height = videoRect.height;

  overlay.style.top = `${top}px`;
  overlay.style.left = `${left}px`;
  overlay.style.width = `${width}px`;
  overlay.style.height = `${height}px`;

  // Calculate 9:16 crop width based on aspect ratio math
  const cropWidth = height * (9 / 16);
  
  // Crop window offset boundaries:
  const maxOffset = width - cropWidth;
  const cropLeft = (cropOffsetPct / 100) * maxOffset;

  const maskLeft = document.getElementById('crop-mask-left');
  const frame = document.getElementById('crop-frame');
  const maskRight = document.getElementById('crop-mask-right');
  const facecamBox = document.getElementById('kick-clipper-facecam-box');

  if (maskLeft && frame && maskRight) {
    maskLeft.style.width = `${cropLeft}px`;
    frame.style.width = `${cropWidth}px`;
    frame.style.left = `${cropLeft}px`;
    maskRight.style.left = `${cropLeft + cropWidth}px`;
    maskRight.style.width = `${width - (cropLeft + cropWidth)}px`;
  }

  // Handle Facecam overlay visibility & bounds
  if (facecamBox) {
    if (aspectMode === 'split_screen') {
      facecamBox.classList.remove('hidden');
      
      const fx = (facecamX / 100) * width;
      const fy = (facecamY / 100) * height;
      const fw = (facecamW / 100) * width;
      const fh = (facecamH / 100) * height;
      
      facecamBox.style.left = `${fx}px`;
      facecamBox.style.top = `${fy}px`;
      facecamBox.style.width = `${fw}px`;
      facecamBox.style.height = `${fh}px`;
    } else {
      facecamBox.classList.add('hidden');
    }
  }
}

function removeCropOverlay() {
  const overlay = document.getElementById('kick-clipper-crop-overlay');
  if (overlay) {
    overlay.remove();
  }
}

// Fetch and render the local clip gallery
function renderGallery() {
  const container = document.getElementById('clipper-tab-content-gallery');
  if (!container) return;

  container.innerHTML = `<div class="gallery-loading">Loading local clip library...</div>`;

  fetch('http://localhost:8000/list-clips')
    .then(res => res.json())
    .then(clips => {
      if (!clips || clips.length === 0) {
        container.innerHTML = `
          <div class="gallery-empty">
            <span class="gallery-empty-icon">📂</span>
            <p>Your local clip library is empty.</p>
            <p class="subtitle">Generate clips in the editor tab to fill your library.</p>
          </div>
        `;
        return;
      }

      let html = `<div class="gallery-grid">`;
      
      clips.forEach(clip => {
        const sizeMB = (clip.size / (1024 * 1024)).toFixed(2);
        const createdDate = new Date(clip.created * 1000).toLocaleString();
        const clipUrl = `http://localhost:8000/clips/${encodeURIComponent(clip.filename)}`;

        html += `
          <div class="gallery-card" data-filename="${clip.filename}">
            <div class="gallery-card-preview">
              <video src="${clipUrl}" controls preload="metadata"></video>
            </div>
            <div class="gallery-card-body">
              <div class="gallery-card-title" title="${clip.filename}">${clip.filename}</div>
              <div class="gallery-card-meta">
                <span>Size: ${sizeMB} MB</span>
                <span>Created: ${createdDate}</span>
              </div>
              <div class="gallery-card-actions">
                <a href="${clipUrl}" download class="btn-card-primary" title="Save to disk">Download</a>
                <button class="btn-card-copy" data-url="${clipUrl}">Copy Link</button>
                <button class="btn-card-delete" data-filename="${clip.filename}">Delete</button>
              </div>
            </div>
          </div>
        `;
      });

      html += `</div>`;
      container.innerHTML = html;

      // Bind copy link listeners
      container.querySelectorAll('.btn-card-copy').forEach(btn => {
        btn.addEventListener('click', (e) => {
          const url = e.target.getAttribute('data-url');
          navigator.clipboard.writeText(url).then(() => {
            const originalText = e.target.textContent;
            e.target.textContent = 'Copied!';
            e.target.style.color = '#53fc18';
            setTimeout(() => {
              e.target.textContent = originalText;
              e.target.style.color = '';
            }, 1500);
          });
        });
      });

      // Bind delete clip listeners
      container.querySelectorAll('.btn-card-delete').forEach(btn => {
        btn.addEventListener('click', (e) => {
          const filename = e.target.getAttribute('data-filename');
          if (confirm(`Are you sure you want to delete ${filename}?`)) {
            fetch(`http://localhost:8000/delete-clip?filename=${encodeURIComponent(filename)}`)
              .then(res => res.json())
              .then(res => {
                if (res && res.success) {
                  renderGallery();
                }
              })
              .catch(err => console.error('Error deleting clip:', err));
          }
        });
      });

    })
    .catch(err => {
      console.error('Error listing clips:', err);
      container.innerHTML = `
        <div class="gallery-error">
          <p>Failed to connect to local library server.</p>
          <p class="subtitle">Ensure Python server is running at http://localhost:8000</p>
        </div>
      `;
    });
}

// Attach event handlers to controls
function setupEventListeners(video) {
  const startSlider = document.getElementById('clipper-start-slider');
  const endSlider = document.getElementById('clipper-end-slider');
  const startInput = document.getElementById('clipper-start-input');
  const endInput = document.getElementById('clipper-end-input');
  const setStartBtn = document.getElementById('btn-set-start-current');
  const setEndBtn = document.getElementById('btn-set-end-current');
  const generateBtn = document.getElementById('btn-generate-clip');

  // Tab buttons and containers
  const tabBtnEditor = document.getElementById('tab-btn-editor');
  const tabBtnGallery = document.getElementById('tab-btn-gallery');
  const tabContentEditor = document.getElementById('clipper-tab-content-editor');
  const tabContentGallery = document.getElementById('clipper-tab-content-gallery');

  // Dynamic layout controls
  const layoutSelect = document.getElementById('clipper-layout-mode');
  const cropSliderRow = document.getElementById('crop-slider-row');
  const cropSlider = document.getElementById('clipper-crop-slider');
  const cropDisplay = document.getElementById('crop-offset-display');
  const volumeSlider = document.getElementById('clipper-volume-slider');
  const volumeDisplay = document.getElementById('volume-level-display');

  if (!startSlider || !endSlider || !startInput || !endInput || !setStartBtn || !setEndBtn || !generateBtn) {
    return;
  }

  // Tabs switching listeners
  if (tabBtnEditor && tabBtnGallery && tabContentEditor && tabContentGallery) {
    tabBtnEditor.addEventListener('click', () => {
      activeTab = 'editor';
      tabBtnEditor.classList.add('active');
      tabBtnGallery.classList.remove('active');
      tabContentEditor.classList.remove('hidden');
      tabContentGallery.classList.add('hidden');
      updateCropOverlay(video);
    });

    tabBtnGallery.addEventListener('click', () => {
      activeTab = 'gallery';
      tabBtnGallery.classList.add('active');
      tabBtnEditor.classList.remove('active');
      tabContentGallery.classList.remove('hidden');
      tabContentEditor.classList.add('hidden');
      removeCropOverlay();
      renderGallery();
    });
  }

  // Layout mode switcher listener
  if (layoutSelect && cropSliderRow) {
    layoutSelect.addEventListener('change', (e) => {
      aspectMode = e.target.value;
      if (aspectMode === '16:9') {
        cropSliderRow.classList.add('hidden');
        removeCropOverlay();
      } else {
        cropSliderRow.classList.remove('hidden');
        if (cropDisplay) cropDisplay.textContent = `${cropOffsetPct}% (Center)`;
        updateCropOverlay(video);
      }
    });
  }

  // Crop positioning listener
  if (cropSlider && cropDisplay) {
    cropSlider.addEventListener('input', (e) => {
      cropOffsetPct = parseInt(e.target.value);
      if (cropOffsetPct === 50) {
        cropDisplay.textContent = '50% (Center)';
      } else if (cropOffsetPct < 50) {
        cropDisplay.textContent = `${cropOffsetPct}% (Left)`;
      } else {
        cropDisplay.textContent = `${cropOffsetPct}% (Right)`;
      }
      updateCropOverlay(video);
    });
  }

  // Audio level slider listener
  if (volumeSlider && volumeDisplay) {
    volumeSlider.addEventListener('input', (e) => {
      const vol = e.target.value;
      if (vol === '0') {
        volumeDisplay.textContent = 'Muted';
        volumeDisplay.style.color = '#ff453a';
      } else if (vol === '100') {
        volumeDisplay.textContent = '100% (Normal)';
        volumeDisplay.style.color = '';
      } else if (parseInt(vol) > 100) {
        volumeDisplay.textContent = `${vol}% (Boosted)`;
        volumeDisplay.style.color = '#53fc18';
      } else {
        volumeDisplay.textContent = `${vol}%`;
        volumeDisplay.style.color = '';
      }
    });
  }

  // Helper to sync changes and seek video
  const handleStartChange = (val) => {
    let numericVal = parseFloat(val);
    if (isNaN(numericVal)) return;

    if (numericVal < currentMin) numericVal = currentMin;
    if (numericVal > currentMax) numericVal = currentMax;

    startVal = numericVal;

    // If start offset pushes past end offset, slide the end offset forward
    if (startVal > endVal) {
      endVal = startVal;
      endSlider.value = endVal;
      endInput.value = Math.floor(endVal);
    }

    startSlider.value = startVal;
    startInput.value = Math.floor(startVal);
    
    video.currentTime = startVal;
    updateUI(video);
  };

  const handleEndChange = (val) => {
    let numericVal = parseFloat(val);
    if (isNaN(numericVal)) return;

    if (numericVal < currentMin) numericVal = currentMin;
    if (numericVal > currentMax) numericVal = currentMax;

    endVal = numericVal;

    // If end offset pulls behind start offset, pull the start offset backward
    if (endVal < startVal) {
      startVal = endVal;
      startSlider.value = startVal;
      startInput.value = Math.floor(startVal);
    }

    endSlider.value = endVal;
    endInput.value = Math.floor(endVal);
    
    video.currentTime = endVal;
    updateUI(video);
  };

  // Slider inputs (seeking)
  startSlider.addEventListener('input', (e) => handleStartChange(e.target.value));
  endSlider.addEventListener('input', (e) => handleEndChange(e.target.value));

  // Precision number inputs
  startInput.addEventListener('change', (e) => handleStartChange(e.target.value));
  endInput.addEventListener('change', (e) => handleEndChange(e.target.value));

  // "Set Current" buttons
  setStartBtn.addEventListener('click', () => {
    handleStartChange(video.currentTime);
  });

  setEndBtn.addEventListener('click', () => {
    handleEndChange(video.currentTime);
  });

  // Action Button
  generateBtn.addEventListener('click', handleGenerateClip);

  // Timeupdate listener for playhead sync
  timeUpdateListener = () => {
    updatePlayhead(video);
  };
  video.addEventListener('timeupdate', timeUpdateListener);

  updateClipperRanges(video);
}

// Update the sliders' ranges based on the video's active timeline/buffer
function updateClipperRanges(video) {
  const startSlider = document.getElementById('clipper-start-slider');
  const endSlider = document.getElementById('clipper-end-slider');
  const startInput = document.getElementById('clipper-start-input');
  const endInput = document.getElementById('clipper-end-input');

  if (!startSlider || !endSlider || !startInput || !endInput) return;

  const minTime = video.seekable && video.seekable.length > 0 ? video.seekable.start(0) : 0;
  
  let maxTime = video.duration;
  const isLive = isNaN(maxTime) || maxTime === Infinity;
  if (isLive) {
    maxTime = video.seekable && video.seekable.length > 0 ? video.seekable.end(0) : video.currentTime || 3600;
  }

  currentMin = minTime;
  currentMax = maxTime;

  startSlider.min = minTime;
  startSlider.max = maxTime;
  endSlider.min = minTime;
  endSlider.max = maxTime;

  startInput.min = Math.floor(minTime);
  startInput.max = Math.ceil(maxTime);
  endInput.min = Math.floor(minTime);
  endInput.max = Math.ceil(maxTime);

  const isFirstLoad = startVal === 0 && endVal === 0;
  if (isFirstLoad) {
    startVal = minTime;
    endVal = Math.min(minTime + 60, maxTime);
    
    startSlider.value = startVal;
    endSlider.value = endVal;
    startInput.value = Math.floor(startVal);
    endInput.value = Math.ceil(endVal);
  }

  if (startVal < minTime) {
    startVal = minTime;
    startSlider.value = startVal;
    startInput.value = Math.floor(startVal);
  }
  if (endVal > maxTime) {
    endVal = maxTime;
    endSlider.value = endVal;
    endInput.value = Math.ceil(endVal);
  }
  if (startVal > endVal) {
    startVal = endVal;
    startSlider.value = startVal;
    startInput.value = Math.floor(startVal);
  }

  updateUI(video);
}

// Synchronize all visual markers, labels and inputs
function updateUI(video) {
  const startLabel = document.getElementById('timeline-start-label');
  const endLabel = document.getElementById('timeline-end-label');
  const startTimeDisplay = document.getElementById('start-time-display');
  const endTimeDisplay = document.getElementById('end-time-display');
  const durationDisplay = document.getElementById('clip-duration-display');
  const highlight = document.getElementById('timeline-highlight');

  if (!startLabel || !endLabel || !startTimeDisplay || !endTimeDisplay || !durationDisplay || !highlight) return;

  startLabel.textContent = formatTime(currentMin);
  endLabel.textContent = formatTime(currentMax);
  startTimeDisplay.textContent = formatTime(startVal);
  endTimeDisplay.textContent = formatTime(endVal);

  const duration = endVal - startVal;
  durationDisplay.textContent = `${duration.toFixed(2)}s (${formatTime(duration)})`;

  const totalRange = currentMax - currentMin;
  if (totalRange > 0) {
    const leftPct = ((startVal - currentMin) / totalRange) * 100;
    const widthPct = ((endVal - startVal) / totalRange) * 100;
    
    highlight.style.left = `${leftPct}%`;
    highlight.style.width = `${widthPct}%`;
  } else {
    highlight.style.left = '0%';
    highlight.style.width = '100%';
  }

  updatePlayhead(video);
}

// Handle showing notifications to users
function showNotification(message, type = 'info') {
  const notification = document.getElementById('clipper-notification');
  if (!notification) return;

  notification.className = `clipper-notification clipper-notification-${type}`;
  notification.textContent = message;
  notification.classList.remove('hidden');

  const timeout = type === 'error' ? 12000 : 5000;
  clearTimeout(notification.timeoutId);
  notification.timeoutId = setTimeout(() => {
    notification.classList.add('hidden');
  }, timeout);
}

// Handle generation request to the API
function handleGenerateClip() {
  const generateBtn = document.getElementById('btn-generate-clip');
  const spinner = document.getElementById('clipper-btn-spinner');

  if (!generateBtn || generateBtn.disabled) return;

  // Set loading UI states
  generateBtn.disabled = true;
  spinner.classList.remove('hidden');
  showNotification('Fetching stream manifest URL...', 'info');

  // Request the captured stream URL from the background worker
  chrome.runtime.sendMessage({ action: 'get_stream_url' }, (response) => {
    const streamUrl = response ? response.streamUrl : null;

    if (!streamUrl) {
      showNotification('Error: Could not retrieve stream URL. Ensure the stream is active, or reload the tab.', 'error');
      generateBtn.disabled = false;
      spinner.classList.add('hidden');
      return;
    }

    const duration = endVal - startVal;
    
    showNotification('Contacting clipping API to cut the stream...', 'info');

    // Send download command to background script
    chrome.runtime.sendMessage({
      action: 'download_clip',
      streamUrl: streamUrl,
      startVal: Math.floor(startVal),
      duration: Math.ceil(duration),
      layoutMode: aspectMode === 'split_screen' ? 'split_screen' : (aspectMode === '9:16' ? 'vertical_crop' : 'widescreen'),
      cropOffsetPct: cropOffsetPct,
      facecamXPct: facecamX.toFixed(2),
      facecamYPct: facecamY.toFixed(2),
      facecamWPct: facecamW.toFixed(2),
      facecamHPct: facecamH.toFixed(2),
      watermarkText: document.getElementById('clipper-watermark-text').value,
      watermarkPos: document.getElementById('clipper-watermark-pos').value,
      audioVolume: parseInt(document.getElementById('clipper-volume-slider').value),
      resolution: document.getElementById('clipper-resolution-select').value
    }, (downloadResponse) => {
      // Re-enable UI and clear spinner
      generateBtn.disabled = false;
      spinner.classList.add('hidden');

      if (downloadResponse && downloadResponse.success) {
        showNotification('Download started! Check your browser downloads.', 'success');
      } else {
        const errMsg = (downloadResponse && downloadResponse.error) ? downloadResponse.error : 'Download failed to start.';
        console.error('Clipper Generation Failed:', errMsg);
        showNotification(`API Error: ${errMsg}. Make sure the backend server at localhost:8000 is active.`, 'error');
      }
    });
  });
}

// Clean up references and DOM elements
function teardownClipper() {
  if (clipperContainer) {
    clipperContainer.remove();
    clipperContainer = null;
  }
  if (activeVideo && timeUpdateListener) {
    activeVideo.removeEventListener('timeupdate', timeUpdateListener);
  }
  removeCropOverlay();
  activeVideo = null;
  activeVideoSrc = null;
  timeUpdateListener = null;
  startVal = 0;
  endVal = 0;
  activeTab = 'editor';
  aspectMode = '16:9';
  cropOffsetPct = 50;
  facecamX = 10;
  facecamY = 10;
  facecamW = 25;
  facecamH = 25;
}

// Main execution cycle
function checkPlayer() {
  const video = document.querySelector('video');

  if (!video) {
    if (clipperContainer) {
      console.log('Video element lost. Tearing down clipper overlay.');
      teardownClipper();
    }
    return;
  }

  // Handle SPA transitions / frame swaps and source shifts
  const isNewVideo = video !== activeVideo || video.currentSrc !== activeVideoSrc;
  const isUIMissing = !document.getElementById('kick-custom-clipper');

  if (isNewVideo || isUIMissing) {
    console.log('New video player or source transition detected. Re-initializing clipper UI...');
    teardownClipper();
    activeVideo = video;
    activeVideoSrc = video.currentSrc;
    injectClipper(video);
  } else {
    // Only update sliders range if active tab is the editor
    if (activeTab === 'editor') {
      updateClipperRanges(video);
      updateCropOverlay(video);
    }
    
    // Periodically verify if background worker has captured a URL
    if (Math.random() < 0.25) { 
      queryStreamUrl();
    }
  }
}

// Initial script execution
function init() {
  console.log('Kick Stream Clipper content script v2.0.0 (Split-Screen & Gallery edition) initialized.');
  pollIntervalId = setInterval(checkPlayer, 1000);
}

init();
