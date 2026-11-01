/* Shared device label for login and linked-device sessions. */
(function (root) {
  'use strict';

  function detect(ua = '', maxTouchPoints = 0) {
    const browser = /Edg(?:e|A|iOS)?\//.test(ua) ? 'Edge'
      : /(?:Chrome|CriOS)\//.test(ua) ? 'Chrome'
      : /(?:Firefox|FxiOS)\//.test(ua) ? 'Firefox'
      : /Safari\//.test(ua) ? 'Safari' : 'Browser';
    let platform = 'unknown';
    let deviceType = 'unknown';
    let deviceName = 'Web browser';
    if (/Android/.test(ua)) {
      platform = 'Android';
      deviceType = /Mobile/.test(ua) ? 'phone' : 'tablet';
      deviceName = 'Android ' + (deviceType === 'phone' ? 'phone' : 'tablet');
    } else if (/iPad/.test(ua) || (/Macintosh|Mac OS X/.test(ua) && maxTouchPoints > 1 && !/iPhone|iPod/.test(ua))) {
      platform = 'iPadOS';
      deviceType = 'tablet';
      deviceName = 'iPad';
    } else if (/iPhone|iPod/.test(ua)) {
      platform = 'iOS';
      deviceType = 'phone';
      deviceName = /iPod/.test(ua) ? 'iPod' : 'iPhone';
    } else {
      platform = /Windows/.test(ua) ? 'Windows' : /Macintosh|Mac OS X/.test(ua) ? 'macOS'
        : /Linux/.test(ua) ? 'Linux' : 'unknown';
      if (platform !== 'unknown') {
        deviceType = 'desktop';
        deviceName = platform === 'macOS' ? 'Mac computer'
          : platform === 'Windows' ? 'Windows computer' : 'Linux computer';
      }
    }
    const label = platform === 'unknown'
      ? (browser === 'Browser' ? deviceName : deviceName + ' · ' + browser)
      : deviceName + ' · ' + browser;
    return { platform, deviceType, deviceName, browser, label };
  }

  root.CX = root.CX || {};
  root.CX.deviceInfo = { detect };
})(typeof window !== 'undefined' ? window : globalThis);
