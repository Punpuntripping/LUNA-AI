import { StyleSheet, Text, View } from "react-native";

import { CONTRACTS_VERSION } from "@contracts";

export default function Index() {
  return (
    <View style={styles.container}>
      <Text style={styles.title}>ريحان</Text>
      <Text style={styles.meta}>contracts v{CONTRACTS_VERSION}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  title: {
    fontSize: 32,
    fontWeight: "600",
  },
  meta: {
    marginTop: 8,
    fontSize: 12,
    opacity: 0.5,
  },
});
