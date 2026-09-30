// SOFTWARE_FIXTURE_ONLY: 2x2 lossless VP9 canary, alpha [0, 255, 255, 0].
// This capability probe is never substituted for authored media.
let capability;

export function supportsVp9Alpha() {
  if (capability) return capability;
  capability = new Promise((resolve) => {
    const video = document.createElement('video');
    let settled = false;
    const finish = (supported) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      video.onloadeddata = null;
      video.onerror = null;
      video.pause();
      video.removeAttribute('src');
      video.load();
      if (supported === null) capability = null;
      resolve(supported);
    };
    const timeout = setTimeout(() => finish(null), 5000);
    video.muted = true;
    video.playsInline = true;
    video.preload = 'auto';
    video.onerror = () => finish(null);
    video.onloadeddata = () => {
      try {
        const canvas = document.createElement('canvas');
        canvas.width = 2;
        canvas.height = 2;
        const context = canvas.getContext('2d');
        context.drawImage(video, 0, 0);
        const rgba = context.getImageData(0, 0, 2, 2).data;
        if (video.videoWidth !== 2 || video.videoHeight !== 2) { finish(null); return; }
        const alpha = [3, 7, 11, 15].map(index => rgba[index]);
        finish([0, 255, 255, 0].every((value, index) => alpha[index] === value)
          ? true : alpha.every(value => value === 255) ? false : null);
      } catch (error) { finish(null); }
    };
    video.src = CANARY;
  });
  return capability;
}
const CANARY = "data:video/webm;base64,GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGFOAZwEAAAAAAALZEU2bdLpNu4tTq4QVSalmU6yBoU27i1OrhBZUrmtTrIHYTbuMU6uEElTDZ1OsggEpTbuMU6uEHFO7a1OsggLD7AEAAAAAAABZAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAVSalmsirXsYMPQkBNgI1MYXZmNjIuMTIuMTAwV0GNTGF2ZjYyLjEyLjEwMESJiEBk4AAAAAAAFlSua8yuAQAAAAAAAEPXgQFzxYhTI426fo875pyBACK1nIN1bmSIgQCGhVZfVlA5g4EBI+ODhAJ7yGrglLCBArqBApqBAlPAgQFVsIRVuYEBElTDZ0CAc3OgY8CAZ8iaRaOHRU5DT0RFUkSHjUxhdmY2Mi4xMi4xMDBzc9pjwItjxYhTI426fo875mfIpUWjh0VOQ09ERVJEh5hMYXZjNjIuMjguMTAwIGxpYnZweC12cDlnyKFFo4hEVVJBVElPTkSHkzAwOjAwOjAwLjE2NzAwMDAwMAAfQ7Z1QQ7ngQCg9qGtgQAAAIJJg0IAABAAFgA4JBwYAAAAIAAAEb///1CIKb////gsU/////+ooQAAdaHEpsLugQGlvYJJg0IAABAAFgA4JBwYAAAAIAAAIMv///O5+jwd5efmO/6oO+8vOcj9vN8us7F71rHp0vSle6p+eD9uhACgr6GSgQAqAIYAQJKcAEAAAAIAAEtAdaGVppPugQGljoYAQJKcAEAAAAIAAEtA+4HWoK+hkoEAUwCGAECSnABAAAACAABLQHWhlaaT7oEBpY6GAECSnABAAAACAABLQPuB16CvoZKBAH0AhgBAkpwAQAAAAgAAS0B1oZWmk+6BAaWOhgBAkpwAQAAAAgAAS0D7gdYcU7trkbuPs4EAt4r3gQHxggGv8IED";
