(() => {
  "use strict";
  const form = document.getElementById("identify-form");
  if (!form) return;
  const video = document.getElementById("camera");
  const message = document.getElementById("camera-message");
  const start = document.getElementById("camera-start");
  const stop = document.getElementById("camera-stop");
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d", {willReadFrequently: true});
  let stream = null, timer = null, scanning = false, version = 0;
  function close() {
    version += 1;
    scanning = false;
    clearTimeout(timer);
    if (stream) stream.getTracks().forEach(track => track.stop());
    stream = null;
    video.srcObject = null;
    video.hidden = true;
    start.disabled = false;
    stop.hidden = true;
  }
  function decode() {
    if (!scanning) return;
    if (video.readyState >= 2 && video.videoWidth && context) {
      const scale = Math.min(1, 640 / video.videoWidth);
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const frame = context.getImageData(0, 0, canvas.width, canvas.height);
      const found = window.jsQR(frame.data, frame.width, frame.height, {inversionAttempts: "dontInvert"});
      if (found && found.data) {
        close();
        if (found.data.length > 128) {
          message.textContent = "二维码内容不符合员工标识格式，请核对。";
          return;
        }
        form.elements.method.value = "qr";
        form.elements.credential.value = found.data;
        message.textContent = "已识别，正在核对员工身份…";
        form.requestSubmit();
        return;
      }
    }
    timer = setTimeout(decode, 200);
  }
  start.addEventListener("click", async () => {
    close();
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      message.textContent = "摄像头需要 HTTPS 或本机 localhost 访问，请联系管理员配置。";
      return;
    }
    start.disabled = true;
    const requestVersion = version;
    message.textContent = "正在打开摄像头…";
    try {
      const incoming = await navigator.mediaDevices.getUserMedia({video: {facingMode: {ideal: "environment"}}, audio: false});
      if (requestVersion !== version || document.hidden) { incoming.getTracks().forEach(track => track.stop()); return; }
      stream = incoming;
      video.srcObject = stream;
      video.hidden = false;
      await video.play();
      if (requestVersion !== version) return;
      scanning = true;
      stop.hidden = false;
      message.textContent = "请将员工二维码放在镜头前，识别后自动进入。";
      decode();
    } catch (error) {
      close();
      message.textContent = error.name === "NotAllowedError" ? "未获得摄像头权限，请在浏览器中允许后重试。" : "无法打开摄像头，请检查连接，或使用外接设备输入标识。";
    }
  });
  stop.addEventListener("click", () => { close(); message.textContent = "摄像头已关闭。"; });
  form.elements.method.addEventListener("change", () => { close(); form.elements.credential.value = ""; });
  window.addEventListener("pagehide", close);
  document.addEventListener("visibilitychange", () => { if (document.hidden) close(); });
})();
