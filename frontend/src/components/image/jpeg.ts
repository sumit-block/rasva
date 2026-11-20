/**
 * JPEG export in the browser (ADR-0007): the app's Python has no Pillow, and
 * every browser that can open Rasva (the Mac and paired phones) has a JPEG
 * encoder in <canvas>. Canvas output carries no metadata at all; when the
 * owner keeps generation details, a JPEG comment (COM) segment with the same
 * short provenance label as the PNG export is added after the start marker.
 */

const MAX_COMMENT = 1000;

export function commentText(details: Record<string, string>): string {
  return Object.entries(details)
    .map(([k, v]) => `${k}: ${v}`)
    .join("\n")
    .replace(/[^\x20-\x7e\n]/g, "?")
    .slice(0, MAX_COMMENT);
}

/** Insert a COM segment right after SOI (FF D8). */
export function withComment(jpeg: Uint8Array, text: string): Uint8Array {
  if (jpeg[0] !== 0xff || jpeg[1] !== 0xd8) throw new Error("Not a JPEG file.");
  const body = new TextEncoder().encode(text);
  const len = body.length + 2;
  const out = new Uint8Array(jpeg.length + 2 + len);
  out.set(jpeg.subarray(0, 2), 0);
  out.set([0xff, 0xfe, len >> 8, len & 0xff], 2);
  out.set(body, 6);
  out.set(jpeg.subarray(2), 6 + body.length);
  return out;
}

export async function encodeJpeg(src: string, quality: number): Promise<Uint8Array> {
  const res = await fetch(src);
  if (!res.ok) throw new Error("Couldn't load the image to export.");
  const bitmap = await createImageBitmap(await res.blob());
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("This browser can't make JPEG files.");
  ctx.fillStyle = "#ffffff"; // JPEG has no transparency
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(bitmap, 0, 0);
  bitmap.close();
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
  if (!blob) throw new Error("This browser couldn't make the JPEG file.");
  return new Uint8Array(await blob.arrayBuffer());
}

export function saveBytes(bytes: Uint8Array, name: string, type: string) {
  const url = URL.createObjectURL(new Blob([bytes as BlobPart], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
