import { useEffect, useState } from "react";
import { Keyboard, Platform } from "react-native";

/** Height of the on-screen keyboard (0 while hidden). Built-in RN events, so
 *  it also works inside a RN Modal (its own window on Android). iOS reports
 *  on `will` (before the slide), Android only on `did`. */
export function useKeyboardHeight(): number {
  const [height, setHeight] = useState(0);
  useEffect(() => {
    const showEvent = Platform.OS === "ios" ? "keyboardWillShow" : "keyboardDidShow";
    const hideEvent = Platform.OS === "ios" ? "keyboardWillHide" : "keyboardDidHide";
    const show = Keyboard.addListener(showEvent, (e) => setHeight(e.endCoordinates.height));
    const hide = Keyboard.addListener(hideEvent, () => setHeight(0));
    return () => {
      show.remove();
      hide.remove();
    };
  }, []);
  return height;
}
