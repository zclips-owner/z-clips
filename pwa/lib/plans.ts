export const PLANS = {
  FREE: {
    name: "Free",
    pricePKR: 0,
    monthlyCredits: 5,
    manualEditing: false,
    captionStyles: 1,
    youtubeMonitoring: false,
    export4K: false
  },
  LITE: {
    name: "Monthly Lite",
    pricePKR: 1500,
    monthlyCredits: 20,
    manualEditing: true,
    captionStyles: 3,
    youtubeMonitoring: false,
    export4K: false
  },
  PRO: {
    name: "Monthly Pro",
    pricePKR: 7000,
    monthlyCredits: 70,
    manualEditing: true,
    captionStyles: "all" as const,
    youtubeMonitoring: true,
    export4K: true
  }
} as const;

export type PlanKey = keyof typeof PLANS;
