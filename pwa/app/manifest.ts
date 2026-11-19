import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Z CLIPS",
    short_name: "Z CLIPS",
    description: "AI clip creation and management",
    start_url: "/dashboard",
    display: "standalone",
    background_color: "#0b0910",
    theme_color: "#100b19",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" }
    ]
  };
}
