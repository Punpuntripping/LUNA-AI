import { useCallback, useEffect, useState } from "react";

/** Seconds-left countdown for «إعادة الإرسال» (60 s = the backend's
 *  OTP_REQUEST_COOLDOWN_S). `start()` (re)arms it. */
export function useCooldown(seconds: number, startArmed = false) {
  const [secondsLeft, setSecondsLeft] = useState(startArmed ? seconds : 0);

  useEffect(() => {
    if (secondsLeft <= 0) return;
    const t = setTimeout(() => setSecondsLeft((s) => s - 1), 1000);
    return () => clearTimeout(t);
  }, [secondsLeft]);

  const start = useCallback(() => setSecondsLeft(seconds), [seconds]);
  return { secondsLeft, active: secondsLeft > 0, start };
}
