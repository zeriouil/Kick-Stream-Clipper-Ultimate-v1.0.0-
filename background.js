// Service worker for Kick Stream Clipper
// Responsible for intercepting playlist URLs and persisting them per-tab.

// Pattern to match Kick.com streaming playlists (.m3u8 formats)
const STREAM_URL_PATTERN = /\.(m3u8|master\.m3u8|index-dvr\.m3u8)(\?|$)/i;

console.log('Kick Stream Clipper background service worker v2.0.0 (Ultimate Suite) active.');

// Monitor network requests to capture playlist URLs
chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    const { url, tabId } = details;

    // Ignore requests not associated with an active tab
    if (!tabId || tabId === -1) return;

    if (STREAM_URL_PATTERN.test(url)) {
      console.log(`Intercepted stream playlist for Tab ${tabId}:`, url);
      
      // Store the stream URL using chrome.storage.local to persist across
      // ephemeral service worker lifecycles in Manifest V3.
      chrome.storage.local.set({ [`stream_url_${tabId}`]: url }, () => {
        if (chrome.runtime.lastError) {
          console.error('Error saving stream URL to storage:', chrome.runtime.lastError);
        }
      });
    }
  },
  {
    urls: [
      "*://*.kick.com/*",
      "*://*.cloudfront.net/*"
    ]
  }
);

// Listen for messages from content scripts
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === 'get_stream_url') {
    const tabId = sender.tab ? sender.tab.id : null;
    
    if (!tabId) {
      console.warn('get_stream_url requested but no sender tab ID found.');
      sendResponse({ streamUrl: null, error: 'No active tab ID found' });
      return;
    }

    // Retrieve the stream URL stored for this specific tab
    chrome.storage.local.get([`stream_url_${tabId}`], (result) => {
      const streamUrl = result[`stream_url_${tabId}`] || null;
      console.log(`Responding with stream URL for Tab ${tabId}:`, streamUrl);
      sendResponse({ streamUrl });
    });

    // Return true to indicate asynchronous response handling
    return true;
  } else if (message.action === 'download_clip') {
    const { 
      streamUrl, 
      startVal, 
      duration, 
      layoutMode, 
      cropOffsetPct, 
      facecamXPct, 
      facecamYPct, 
      facecamWPct, 
      facecamHPct, 
      watermarkText, 
      watermarkPos, 
      audioVolume, 
      resolution 
    } = message;
    
    // Generate a unique filename with ISO date-time representation
    const dateString = new Date().toISOString().replace(/T/, '_').replace(/\..+/, '').replace(/:/g, '-');
    const filename = `kick_clip_${dateString}.mp4`;
    
    // Construct the GET URL with all query parameters
    let downloadUrl = `http://localhost:8000/create-clip?stream_url=${encodeURIComponent(streamUrl)}` + 
                      `&start_offset=${startVal}` + 
                      `&duration_seconds=${duration}`;
    
    if (layoutMode) {
      downloadUrl += `&layout_mode=${encodeURIComponent(layoutMode)}`;
    }
    if (cropOffsetPct !== undefined) {
      downloadUrl += `&crop_offset_pct=${cropOffsetPct}`;
    }
    if (facecamXPct !== undefined) {
      downloadUrl += `&facecam_x_pct=${facecamXPct}`;
    }
    if (facecamYPct !== undefined) {
      downloadUrl += `&facecam_y_pct=${facecamYPct}`;
    }
    if (facecamWPct !== undefined) {
      downloadUrl += `&facecam_w_pct=${facecamWPct}`;
    }
    if (facecamHPct !== undefined) {
      downloadUrl += `&facecam_h_pct=${facecamHPct}`;
    }
    if (watermarkText !== undefined) {
      downloadUrl += `&watermark_text=${encodeURIComponent(watermarkText)}`;
    }
    if (watermarkPos) {
      downloadUrl += `&watermark_pos=${encodeURIComponent(watermarkPos)}`;
    }
    if (audioVolume !== undefined) {
      downloadUrl += `&audio_volume=${audioVolume}`;
    }
    if (resolution) {
      downloadUrl += `&resolution=${encodeURIComponent(resolution)}`;
    }
    
    console.log(`Initiating downloads.download via GET for: ${filename}`);

    chrome.downloads.download({
      url: downloadUrl,
      filename: filename,
      saveAs: true
    }, (downloadId) => {
      if (chrome.runtime.lastError) {
        console.error('Download initiation failed:', chrome.runtime.lastError);
        sendResponse({ success: false, error: chrome.runtime.lastError.message });
      } else {
        console.log('Download initiated successfully, ID:', downloadId);
        sendResponse({ success: true, downloadId: downloadId });
      }
    });
    
    return true; // Keep sendResponse open for async download callback
  }
});

// Clean up stored stream URLs when a tab is closed to prevent memory leaks
chrome.tabs.onRemoved.addListener((tabId) => {
  const key = `stream_url_${tabId}`;
  chrome.storage.local.remove([key], () => {
    if (chrome.runtime.lastError) {
      console.error(`Error cleaning storage for Tab ${tabId}:`, chrome.runtime.lastError);
    } else {
      console.log(`Cleared stream URL cache for Tab ${tabId} due to closure.`);
    }
  });
});

// Clean up storage when the tab navigates away from Kick.com
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.url) {
    const isKick = changeInfo.url.includes('kick.com') || changeInfo.url.includes('cloudfront.net');
    if (!isKick) {
      chrome.storage.local.remove([`stream_url_${tabId}`], () => {
        if (!chrome.runtime.lastError) {
          console.log(`Cleared stream URL cache for Tab ${tabId} due to navigation away from Kick.`);
        }
      });
    }
  }
});
