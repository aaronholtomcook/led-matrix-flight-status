# rgbmatrix_sim/core.py  (a drop-in stand-in for the real "rgbmatrix" package)
import pygame

class RGBMatrixOptions:
    def __init__(self):
        self.rows = 32
        self.cols = 32
        self.chain_length = 1
        self.parallel = 1
        self.hardware_mapping = "regular"
        self.brightness = 100
        self.pwm_bits = 11
        self.pixel_scale = 12  # sim-only: how big each "LED" is on screen

class Canvas:
    def __init__(self, width, height, pixel_scale):
        self.width = width
        self.height = height
        self.pixel_scale = pixel_scale
        self.pixels = [[(0, 0, 0)] * width for _ in range(height)]

    def SetPixel(self, x, y, r, g, b):
        if 0 <= x < self.width and 0 <= y < self.height:
            self.pixels[y][x] = (r, g, b)

    def Clear(self):
        self.pixels = [[(0, 0, 0)] * self.width for _ in range(self.height)]

    def Fill(self, r, g, b):
        self.pixels = [[(r, g, b)] * self.width for _ in range(self.height)]

class RGBMatrix:
    def __init__(self, options: RGBMatrixOptions):
        self.width = options.cols * options.chain_length
        self.height = options.rows * options.parallel
        self.scale = options.pixel_scale
        pygame.init()
        self.screen = pygame.display.set_mode(
            (self.width * self.scale, self.height * self.scale)
        )
        pygame.display.set_caption("RGB Matrix Simulator")
        self.canvas = Canvas(self.width, self.height, self.scale)

    def CreateFrameCanvas(self):
        return Canvas(self.width, self.height, self.scale)

    def SwapOnVSync(self, canvas: Canvas):
        self.canvas = canvas
        self._draw()
        self.canvas = self.CreateFrameCanvas()
        return self.canvas

    def _draw(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                raise SystemExit
        for y in range(self.canvas.height):
            for x in range(self.canvas.width):
                r, g, b = self.canvas.pixels[y][x]
                rect = pygame.Rect(
                    x * self.scale, y * self.scale, self.scale, self.scale
                )
                pygame.draw.rect(self.screen, (r, g, b), rect)
        pygame.display.flip()


    def Clear(self):
        self.canvas.Clear()
        self._draw()