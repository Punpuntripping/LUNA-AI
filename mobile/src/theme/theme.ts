import {
  darkColours,
  darkShadows,
  lightColours,
  lightShadows,
  radius,
  space,
  type Colours,
  type Shadows,
} from "./tokens.generated";

export type ColourScheme = "light" | "dark";

export interface Theme {
  scheme: ColourScheme;
  colours: Colours;
  shadows: Shadows;
  radius: typeof radius;
  space: typeof space;
}

export const lightTheme: Theme = { scheme: "light", colours: lightColours, shadows: lightShadows, radius, space };
export const darkTheme: Theme = { scheme: "dark", colours: darkColours, shadows: darkShadows, radius, space };
