/*
 * drmcap - capture the live DRM scanout buffer and publish frames in shared memory.
 *
 * Why C: the same downsample+convert loop in Python manages 0.8 fps at 640x360
 * on this SoC. Here it runs in a few milliseconds.
 *
 * Shared memory layout (/dev/shm/drmcap.bin):
 *   struct cap_hdr (64 bytes)
 *   uint8_t buf32[w*h*4]   RGBX, little endian, matches noVNC's default
 *                          pixel format (32bpp, shifts 16/8/0)
 *   uint8_t buf16[w*h*2]   RGB565 little endian, for clients that ask 16bpp
 *
 * The header carries a monotonically increasing seq plus a bounding box of the
 * pixels that changed since the previous frame, so a viewer can send tiny
 * updates for static menus and full frames only during gameplay.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <pthread.h>

#define DRM_IOCTL_BASE 'd'
#define DRM_IOWR(nr, type) _IOWR(DRM_IOCTL_BASE, nr, type)

struct drm_mode_modeinfo {
	uint32_t clock;
	uint16_t hdisplay, hsync_start, hsync_end, htotal, hskew;
	uint16_t vdisplay, vsync_start, vsync_end, vtotal, vscan;
	uint32_t vrefresh, flags, type;
	char name[32];
};

struct drm_mode_card_res {
	uint64_t fb_id_ptr, crtc_id_ptr, connector_id_ptr, encoder_id_ptr;
	uint32_t count_fbs, count_crtcs, count_connectors, count_encoders;
	uint32_t min_width, max_width, min_height, max_height;
};

struct drm_mode_crtc {
	uint64_t set_connectors_ptr;
	uint32_t count_connectors, crtc_id, fb_id, x, y, gamma_size, mode_valid;
	struct drm_mode_modeinfo mode;
};

struct drm_mode_fb_cmd {
	uint32_t fb_id, width, height, pitch, bpp, depth, handle;
};

struct drm_prime_handle {
	uint32_t handle, flags;
	int32_t fd;
};

/* _IO('d', 0x1b): drop DRM master.  The first client to open the card becomes
 * master implicitly; if this capture helper starts before RetroArch (it does at
 * boot now), holding master makes RetroArch fail with "Error when switching
 * mode" and exit.  We only read scanout, so give master away immediately. */
#define DRM_IOCTL_DROP_MASTER _IO(DRM_IOCTL_BASE, 0x1f)
#define DRM_IOCTL_MODE_GETRESOURCES DRM_IOWR(0xA0, struct drm_mode_card_res)
#define DRM_IOCTL_MODE_GETCRTC DRM_IOWR(0xA1, struct drm_mode_crtc)
#define DRM_IOCTL_MODE_GETFB DRM_IOWR(0xAD, struct drm_mode_fb_cmd)
#define DRM_IOCTL_PRIME_HANDLE_TO_FD DRM_IOWR(0x2D, struct drm_prime_handle)

#define CAP_MAGIC 0x43415032u /* "CAP2" */

struct cap_hdr {
	uint32_t magic;
	uint32_t seq;
	uint32_t w, h;
	uint32_t len32, len16;
	uint32_t bx, by, bw, bh;
	uint32_t pad[6];
};

/* ---- B860 multi-core capture (build-5) ----
 * 每线程负责一段连续输出行：WC 帧缓冲行拷贝 + RGBX/RGB565 转换 + 与上一帧的
 * 脏区比对，结果直接写入 shm 输出区（省去主线程每帧 5.5MB memcpy）。
 * 主线程只做 fb 重挂检查、脏区合并、seq 发布与节拍。 */
struct band_ctx { uint32_t bx, by, bw, bh; };
static int nthreads = 4;
static pthread_barrier_t bar_a, bar_b;
static struct band_ctx bands[8];
static uint8_t *t_rowbuf[8];
static volatile int g_first;
static uint8_t *g_src;
static uint32_t g_pitch, g_width;
static uint32_t *g_xmap, *g_ymap;
static int g_ow, g_oh, g_ri, g_bi;
static uint16_t *g_prev;
static uint8_t *g_out32;
static uint16_t *g_out16;
static uint8_t *g_outrgb;

static void *cap_worker(void *arg)
{
	int id = (int)(long)arg;
	struct band_ctx *bk = &bands[id];
	uint8_t *rowbuf = t_rowbuf[id];
	for (;;) {
		pthread_barrier_wait(&bar_a);
		int ow = g_ow, oh = g_oh, ri = g_ri, bi = g_bi;
		int first = g_first;
		int y0 = id * oh / nthreads, y1 = (id + 1) * oh / nthreads;
		uint32_t minx = (uint32_t)ow, maxx = 0, miny = (uint32_t)oh, maxy = 0;
		for (int y = y0; y < y1; y++) {
			memcpy(rowbuf, g_src + (size_t)g_ymap[y] * g_pitch,
			       (size_t)g_width * 4);
			uint8_t *o32 = g_out32 + (size_t)y * ow * 4;
			uint16_t *o16 = g_out16 + (size_t)y * ow;
			uint8_t *oRgb = g_outrgb + (size_t)y * ow * 3;
			uint16_t *pv = g_prev + (size_t)y * ow;
			int rowchg = 0;
			for (int x = 0; x < ow; x++) {
				const uint8_t *p = rowbuf + g_xmap[x];
				uint8_t r = p[ri], g = p[1], b = p[bi];
				*(uint32_t *)o32 = (uint32_t)r | (uint32_t)g << 8 |
						   (uint32_t)b << 16;
				o32 += 4;
				*oRgb++ = r;
				*oRgb++ = g;
				*oRgb++ = b;
				uint16_t v = (uint16_t)(((r & 0xF8) << 8) |
							((g & 0xFC) << 3) | (b >> 3));
				*o16++ = v;
				if (!first && v != pv[x]) {
					rowchg = 1;
					if ((uint32_t)x < minx) minx = (uint32_t)x;
					if ((uint32_t)x > maxx) maxx = (uint32_t)x;
				}
			}
			if (rowchg) {
				if ((uint32_t)y < miny) miny = (uint32_t)y;
				if ((uint32_t)y > maxy) maxy = (uint32_t)y;
			}
		}
		memcpy(g_prev + (size_t)y0 * ow, g_out16 + (size_t)y0 * ow,
		       (size_t)(y1 - y0) * ow * 2);
		bk->bx = minx;
		bk->by = miny;
		bk->bw = (maxx < minx) ? 0 : maxx - minx + 1;
		bk->bh = (maxy < miny) ? 0 : maxy - miny + 1;
		pthread_barrier_wait(&bar_b);
	}
	return NULL;
}

static int find_fb(int fd, uint32_t *fb_out, uint32_t *w_out, uint32_t *h_out)
{
	struct drm_mode_card_res res;
	memset(&res, 0, sizeof(res));
	if (ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &res) < 0) {
		perror("GETRESOURCES");
		return -1;
	}
	if (!res.count_crtcs)
		return -1;

	uint32_t *crtcs = calloc(res.count_crtcs, sizeof(uint32_t));
	if (!crtcs)
		return -1;

	struct drm_mode_card_res r2;
	memset(&r2, 0, sizeof(r2));
	r2.crtc_id_ptr = (uint64_t)(uintptr_t)crtcs;
	r2.count_crtcs = res.count_crtcs;
	if (ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &r2) < 0) {
		free(crtcs);
		return -1;
	}

	int ok = -1;
	for (uint32_t i = 0; i < r2.count_crtcs && ok < 0; i++) {
		struct drm_mode_crtc crtc;
		memset(&crtc, 0, sizeof(crtc));
		crtc.crtc_id = crtcs[i];
		if (ioctl(fd, DRM_IOCTL_MODE_GETCRTC, &crtc) < 0)
			continue;
		if (crtc.fb_id && crtc.mode.hdisplay) {
			*fb_out = crtc.fb_id;
			*w_out = crtc.mode.hdisplay;
			*h_out = crtc.mode.vdisplay;
			/* called every frame to detect fb switches -> only log changes */
			{
				static uint32_t last_fb = 0;
				if (crtc.fb_id != last_fb) {
					last_fb = crtc.fb_id;
					fprintf(stderr, "crtc %u fb %u %ux%u@%u\n",
						crtc.crtc_id, crtc.fb_id,
						crtc.mode.hdisplay,
						crtc.mode.vdisplay,
						crtc.mode.vrefresh);
				}
			}
			ok = 0;
		}
	}
	free(crtcs);
	return ok;
}

int main(int argc, char **argv)
{
	const char *card = "/dev/dri/card0";
	const char *out = "/dev/shm/drmcap.bin";
	const char *ppm = NULL;
	int ow = 960, oh = 540, fps = 5, oneshot = 0, opt, swap_rb = 0;
	int nthreads_opt = 4;

	while ((opt = getopt(argc, argv, "d:o:w:h:f:p:nct:")) != -1) {
		switch (opt) {
		case 'd': card = optarg; break;
		case 'o': out = optarg; break;
		case 'w': ow = atoi(optarg); break;
		case 'h': oh = atoi(optarg); break;
		case 'f': fps = atoi(optarg); break;
		case 'p': ppm = optarg; oneshot = 1; break;
		case 'n': oneshot = 1; break;
		/* -c: framebuffer stores R,G,B,X (XBGR8888) instead of B,G,R,X */
		case 'c': swap_rb = 1; break;
		case 't': nthreads_opt = atoi(optarg); break;
		default:
			fprintf(stderr, "usage: %s [-d card] [-o shm] [-w W] [-h H] [-f fps] [-p out.ppm] [-n] [-t threads]\n",
				argv[0]);
			return 2;
		}
	}

	int fd = open(card, O_RDWR);
	if (fd < 0) {
		perror("open card");
		return 1;
	}
	/* Give up DRM master so RetroArch can still modeset when we start first. */
	if (ioctl(fd, DRM_IOCTL_DROP_MASTER, 0) == 0)
		fprintf(stderr, "drmcap: dropped DRM master\n");
	fprintf(stderr, "drmcap build-4 (dropmaster+rowbuf+dirty)\n");

	uint32_t fbid, sw, sh;
	if (find_fb(fd, &fbid, &sw, &sh) < 0) {
		fprintf(stderr, "no active crtc\n");
		return 1;
	}

	struct drm_mode_fb_cmd fb;
	memset(&fb, 0, sizeof(fb));
	fb.fb_id = fbid;
	if (ioctl(fd, DRM_IOCTL_MODE_GETFB, &fb) < 0) {
		perror("GETFB");
		return 1;
	}

	struct drm_prime_handle ph;
	memset(&ph, 0, sizeof(ph));
	ph.handle = fb.handle;
	ph.fd = -1;
	if (ioctl(fd, DRM_IOCTL_PRIME_HANDLE_TO_FD, &ph) < 0) {
		perror("PRIME_HANDLE_TO_FD");
		return 1;
	}

	size_t fbsize = (size_t)fb.pitch * fb.height;
	uint8_t *src = mmap(NULL, fbsize, PROT_READ, MAP_SHARED, ph.fd, 0);
	if (src == MAP_FAILED) {
		perror("mmap fb");
		return 1;
	}

	if (ow <= 0 || ow > (int)fb.width) ow = fb.width;
	if (oh <= 0 || oh > (int)fb.height) oh = fb.height;

	uint32_t *xmap = malloc(sizeof(uint32_t) * ow);
	uint32_t *ymap = malloc(sizeof(uint32_t) * oh);
	uint8_t *b32 = malloc((size_t)ow * oh * 4);
	uint16_t *b16 = malloc((size_t)ow * oh * 2);
	uint16_t *prev = calloc((size_t)ow * oh, sizeof(uint16_t));
	uint8_t *rowbuf = malloc((size_t)fb.width * 4);
	if (!xmap || !ymap || !b32 || !b16 || !prev || !rowbuf)
		return 1;

	for (int i = 0; i < ow; i++)
		xmap[i] = (uint32_t)((uint64_t)i * fb.width / ow) * 4;
	for (int i = 0; i < oh; i++)
		ymap[i] = (uint32_t)((uint64_t)i * fb.height / oh);

	size_t len32 = (size_t)ow * oh * 4, len16 = (size_t)ow * oh * 2;
	size_t lenrgb = (size_t)ow * oh * 3;
	size_t shmsize = sizeof(struct cap_hdr) + 2 * (len32 + len16 + lenrgb);
	struct cap_hdr *hdr = NULL;
	int shmfd = -1;

	if (!oneshot) {
		shmfd = open(out, O_RDWR | O_CREAT | O_TRUNC, 0666);
		if (shmfd < 0) {
			perror("open shm");
			return 1;
		}
		if (ftruncate(shmfd, shmsize) < 0) {
			perror("ftruncate");
			return 1;
		}
		hdr = mmap(NULL, shmsize, PROT_READ | PROT_WRITE, MAP_SHARED, shmfd, 0);
		if (hdr == MAP_FAILED) {
			perror("mmap shm");
			return 1;
		}
		hdr->magic = CAP_MAGIC;
		hdr->w = ow;
		hdr->h = oh;
		hdr->len32 = len32;
		hdr->len16 = len16;
		hdr->seq = 0;
	}

	/* B860 multi-core: 输出缓冲直接放 shm 区，工作线程分段直写 */
	if (oneshot) {
		g_out32 = b32;
		g_out16 = b16;
		g_outrgb = b32;
		nthreads = 1;
	} else {
		g_out32 = (uint8_t *)hdr + sizeof(struct cap_hdr);
		g_out16 = (uint16_t *)(g_out32 + len32);
		nthreads = nthreads_opt;
		if (nthreads > 8)
			nthreads = 8;
		if (nthreads > oh)
			nthreads = oh;
		if (nthreads < 1)
			nthreads = 1;
	}
	g_prev = prev;
	pthread_barrier_init(&bar_a, NULL, nthreads + 1);
	pthread_barrier_init(&bar_b, NULL, nthreads + 1);
	for (int i = 0; i < nthreads; i++) {
		t_rowbuf[i] = malloc((size_t)fb.width * 4);
		pthread_t th;
		pthread_create(&th, NULL, cap_worker, (void *)(long)i);
	}
	fprintf(stderr, "drmcap build-5 (multicore x%d)\n", nthreads);

	struct timespec next;
	clock_gettime(CLOCK_MONOTONIC, &next);
	long period = fps > 0 ? 1000000000L / fps : 0;
	uint32_t seq = 0;
	int first = 1;
	int cur = 0;
	/* byte offsets of red/blue inside a 32bpp pixel */
	int ri = swap_rb ? 0 : 2;
	int bi = swap_rb ? 2 : 0;

	for (;;) {
		/*
		 * Re-attach if the scanout framebuffer changed.  At boot this helper
		 * starts before RetroArch (multi-user comes before graphical), so we
		 * map the boot-splash fb; when RetroArch modesets it allocates a new
		 * fb and our old mapping would keep showing a frozen splash.
		 */
		{
			uint32_t nfb = 0, nw = 0, nh = 0;
			if (find_fb(fd, &nfb, &nw, &nh) == 0 && nfb != fbid &&
			    (int)nw >= ow && (int)nh >= oh) {
				struct drm_mode_fb_cmd fb2;
				memset(&fb2, 0, sizeof(fb2));
				fb2.fb_id = nfb;
				if (ioctl(fd, DRM_IOCTL_MODE_GETFB, &fb2) == 0) {
					struct drm_prime_handle ph2;
					memset(&ph2, 0, sizeof(ph2));
					ph2.handle = fb2.handle;
					ph2.fd = -1;
					if (ioctl(fd, DRM_IOCTL_PRIME_HANDLE_TO_FD, &ph2) == 0) {
						size_t nsize = (size_t)fb2.pitch * fb2.height;
						uint8_t *nsrc = mmap(NULL, nsize, PROT_READ,
								     MAP_SHARED, ph2.fd, 0);
						if (nsrc != MAP_FAILED) {
							munmap(src, fbsize);
							close(ph.fd);
							src = nsrc;
							fbsize = nsize;
							fb = fb2;
							ph = ph2;
							fbid = nfb;
							sw = nw;
							sh = nh;
							for (int i = 0; i < ow; i++)
								xmap[i] = (uint32_t)((uint64_t)i *
										     fb.width / ow) * 4;
							for (int i = 0; i < oh; i++)
								ymap[i] = (uint32_t)((uint64_t)i *
										     fb.height / oh);
							uint8_t *nb = realloc(rowbuf,
									      (size_t)fb.width * 4);
							if (nb)
								rowbuf = nb;
							memset(prev, 0xff, (size_t)ow * oh * 2);
							for (int ti = 0; ti < nthreads; ti++) {
								uint8_t *nb2 = realloc(t_rowbuf[ti],
									(size_t)fb.width * 4);
								if (nb2)
									t_rowbuf[ti] = nb2;
							}
							first = 1;
							fprintf(stderr,
								"drmcap: re-attached fb %u %ux%u pitch=%u\n",
								nfb, fb.width, fb.height, fb.pitch);
						}
					}
				}
			}
		}

				/*
		 * B860 multi-core capture: 派发本帧给全部工作线程（行拷贝+转换+
		 * 脏区比对直写 shm），barrier 汇合后由主线程合并脏区并发布 seq。
		 */
		g_src = src;
		g_pitch = fb.pitch;
		g_width = fb.width;
		g_xmap = xmap;
		g_ymap = ymap;
		g_ow = ow;
		g_oh = oh;
		g_ri = ri;
		g_bi = bi;
		g_first = first;
		if (oneshot) {
			g_out32 = b32;
			g_out16 = b16;
		} else {
			uint8_t *shm32 = (uint8_t *)hdr + sizeof(struct cap_hdr);
			size_t step = len32 + len16 + lenrgb;
			g_out32 = shm32 + (size_t)cur * step;
			g_out16 = (uint16_t *)(shm32 + (size_t)cur * step + len32);
			g_outrgb = shm32 + (size_t)cur * step + len32 + len16;
		}
		pthread_barrier_wait(&bar_a);
		pthread_barrier_wait(&bar_b);

		if (oneshot) {
			FILE *f = fopen(ppm ? ppm : "/tmp/drmcap.ppm", "wb");
			if (!f) return 1;
			fprintf(f, "P6\n%d %d\n255\n", ow, oh);
			for (int i = 0; i < ow * oh; i++) {
				uint8_t rgb[3] = {b32[i * 4], b32[i * 4 + 1], b32[i * 4 + 2]};   /* RGBX: byte0=R */
				fwrite(rgb, 1, 3, f);
			}
			fclose(f);
			fprintf(stderr, "wrote %s %dx%d\n", ppm ? ppm : "/tmp/drmcap.ppm", ow, oh);
			return 0;
		}

		/* merge per-band dirty boxes */
		uint32_t minx = (uint32_t)ow, maxx = 0, miny = (uint32_t)oh, maxy = 0;
		if (!first) {
			for (int i = 0; i < nthreads; i++) {
				if (bands[i].bw == 0 && bands[i].bh == 0)
					continue;
				if (bands[i].bx < minx) minx = bands[i].bx;
				if (bands[i].by < miny) miny = bands[i].by;
				if (bands[i].bx + bands[i].bw - 1 > maxx)
					maxx = bands[i].bx + bands[i].bw - 1;
				if (bands[i].by + bands[i].bh - 1 > maxy)
					maxy = bands[i].by + bands[i].bh - 1;
			}
		}

		if (first) {
			hdr->bx = 0;
			hdr->by = 0;
			hdr->bw = (uint32_t)ow;
			hdr->bh = (uint32_t)oh;
		} else if (maxx < minx || maxy < miny) {
			hdr->bx = 0;
			hdr->by = 0;
			hdr->bw = 0; /* bw==0 means "nothing changed" */
			hdr->bh = 0;
		} else {
			hdr->bx = minx;
			hdr->by = miny;
			hdr->bw = maxx - minx + 1;
			hdr->bh = maxy - miny + 1;
		}
		hdr->pad[0] = cur;
		hdr->pad[1] = (uint32_t)lenrgb;
		__sync_synchronize();
		hdr->seq = ++seq;

		first = 0;
		cur ^= 1;

		if (period) {
			next.tv_nsec += period;
			while (next.tv_nsec >= 1000000000L) {
				next.tv_nsec -= 1000000000L;
				next.tv_sec++;
			}
			clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);
		}

		if ((seq % 25) == 0) {
			struct timespec now;
			clock_gettime(CLOCK_MONOTONIC, &now);
			static struct timespec t0;
			static int have_t0;
			if (!have_t0) {
				t0 = now;
				have_t0 = 1;
			} else {
				double dt = (now.tv_sec - t0.tv_sec) +
					    (now.tv_nsec - t0.tv_nsec) / 1e9;
				fprintf(stderr, "seq=%u %.1f fps bbox=%u,%u %ux%u\n",
					seq, 25.0 / dt, hdr->bx, hdr->by, hdr->bw,
					hdr->bh);
				t0 = now;
			}
		}
	}
	return 0;
}
