import { useContext } from "react";
import { InvestigatorContext } from "./investigatorContextStore.js";

export function useInvestigator() {
  const ctx = useContext(InvestigatorContext);
  if (!ctx) {
    throw new Error("useInvestigator must be used within an InvestigatorProvider");
  }
  return ctx;
}