import { NextRequest } from "next/server";
import { proxyToFastApi } from "@/lib/proxy";

type RouteContext = { params: Promise<{ slug: string[] }> };

async function handler(req: NextRequest, ctx: RouteContext) {
  const { slug } = await ctx.params;
  return proxyToFastApi(req, slug);
}

export async function GET(req: NextRequest, ctx: RouteContext) {
  return handler(req, ctx);
}

export async function POST(req: NextRequest, ctx: RouteContext) {
  return handler(req, ctx);
}

export async function PUT(req: NextRequest, ctx: RouteContext) {
  return handler(req, ctx);
}

export async function PATCH(req: NextRequest, ctx: RouteContext) {
  return handler(req, ctx);
}

export async function DELETE(req: NextRequest, ctx: RouteContext) {
  return handler(req, ctx);
}
