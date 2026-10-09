"""Small GL 3.3 core kit: programs (with optional geometry stage), buffers, render targets."""
import ctypes

import numpy as np
import OpenGL

OpenGL.ERROR_CHECKING = False
from OpenGL import GL  # noqa: E402


def _compile(src, kind):
    s = GL.glCreateShader(kind)
    GL.glShaderSource(s, src)
    GL.glCompileShader(s)
    if not GL.glGetShaderiv(s, GL.GL_COMPILE_STATUS):
        log = GL.glGetShaderInfoLog(s)
        raise RuntimeError((log.decode() if isinstance(log, bytes) else str(log)) + "\n---\n" + src[:400])
    return s


class Program:
    def __init__(self, vs, fs, gs=None, defines=()):
        head = "#version 330 core\n" + "".join(f"#define {d}\n" for d in defines)
        self.id = GL.glCreateProgram()
        sh = [_compile(head + vs, GL.GL_VERTEX_SHADER), _compile(head + fs, GL.GL_FRAGMENT_SHADER)]
        if gs:
            sh.append(_compile(head + gs, GL.GL_GEOMETRY_SHADER))
        for s in sh:
            GL.glAttachShader(self.id, s)
        GL.glLinkProgram(self.id)
        if not GL.glGetProgramiv(self.id, GL.GL_LINK_STATUS):
            raise RuntimeError(GL.glGetProgramInfoLog(self.id))
        for s in sh:
            GL.glDeleteShader(s)
        self._loc = {}

    def use(self):
        GL.glUseProgram(self.id)
        return self

    def loc(self, name):
        if name not in self._loc:
            self._loc[name] = GL.glGetUniformLocation(self.id, name)
        return self._loc[name]

    def f(self, name, *v):
        (GL.glUniform1f, GL.glUniform2f, GL.glUniform3f, GL.glUniform4f)[len(v) - 1](self.loc(name), *v)

    def i(self, name, v):
        GL.glUniform1i(self.loc(name), v)

    def m4(self, name, m):
        GL.glUniformMatrix4fv(self.loc(name), 1, GL.GL_TRUE, np.ascontiguousarray(m, np.float32))

    def m4v(self, name, ms):
        a = np.ascontiguousarray(ms, np.float32)
        GL.glUniformMatrix4fv(self.loc(name), a.shape[0], GL.GL_TRUE, a)

    def v4v(self, name, vs):
        a = np.ascontiguousarray(vs, np.float32)
        GL.glUniform4fv(self.loc(name), a.shape[0], a)


class Mesh:
    """A VAO over float attribute columns plus optional index buffers (one per primitive)."""

    def __init__(self, attrs, indices=None, dynamic=False):
        """attrs: list of (location, ndarray[N, k]) ; indices: {name: ndarray}"""
        self.vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(self.vao)
        self.vbos = []
        self.usage = GL.GL_DYNAMIC_DRAW if dynamic else GL.GL_STATIC_DRAW
        self.count = 0
        for loc, arr in attrs:
            arr = np.ascontiguousarray(arr, np.float32)
            if arr.ndim == 1:
                arr = arr[:, None]
            vbo = GL.glGenBuffers(1)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
            GL.glBufferData(GL.GL_ARRAY_BUFFER, arr.nbytes, arr, self.usage)
            GL.glEnableVertexAttribArray(loc)
            GL.glVertexAttribPointer(loc, arr.shape[1], GL.GL_FLOAT, False, 0, ctypes.c_void_p(0))
            self.vbos.append((loc, vbo, arr.shape[1]))
            self.count = len(arr)
        self.ibos = {}
        for name, idx in (indices or {}).items():
            idx = np.ascontiguousarray(idx, np.uint32).ravel()
            ibo = GL.glGenBuffers(1)
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ibo)
            GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, idx.nbytes, idx, GL.GL_STATIC_DRAW)
            self.ibos[name] = (ibo, len(idx))
        GL.glBindVertexArray(0)

    def update(self, attrs):
        """Replace all attribute columns (same order as construction)."""
        for (loc, vbo, k), arr in zip(self.vbos, attrs):
            arr = np.ascontiguousarray(arr, np.float32)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
            GL.glBufferData(GL.GL_ARRAY_BUFFER, arr.nbytes, arr, self.usage)
            self.count = len(arr)

    def draw(self, mode, index=None, count=None):
        GL.glBindVertexArray(self.vao)
        if index:
            ibo, n = self.ibos[index]
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ibo)
            GL.glDrawElements(mode, n if count is None else count, GL.GL_UNSIGNED_INT, ctypes.c_void_p(0))
        else:
            GL.glDrawArrays(mode, 0, self.count if count is None else count)

    def delete(self):
        GL.glDeleteVertexArrays(1, [self.vao])
        GL.glDeleteBuffers(len(self.vbos), [v for _, v, _ in self.vbos])
        if self.ibos:
            GL.glDeleteBuffers(len(self.ibos), [b for b, _ in self.ibos.values()])


def texture(w, h, data=None, fmt=GL.GL_RGBA8, src=GL.GL_RGBA, typ=GL.GL_UNSIGNED_BYTE, filt=GL.GL_LINEAR):
    t = GL.glGenTextures(1)
    GL.glBindTexture(GL.GL_TEXTURE_2D, t)
    GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, fmt, w, h, 0, src, typ, data)
    for p, v in ((GL.GL_TEXTURE_MIN_FILTER, filt), (GL.GL_TEXTURE_MAG_FILTER, filt),
                 (GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE), (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)):
        GL.glTexParameteri(GL.GL_TEXTURE_2D, p, v)
    return t


class Target:
    """Colour texture + framebuffer, optionally with depth."""

    def __init__(self, w, h, depth=False, fmt=GL.GL_RGBA16F):
        self.w, self.h = w, h
        self.tex = texture(w, h, None, fmt, GL.GL_RGBA, GL.GL_FLOAT)
        self.fbo = GL.glGenFramebuffers(1)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, self.tex, 0)
        self.rb = None
        if depth:
            self.rb = GL.glGenRenderbuffers(1)
            GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, self.rb)
            GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_DEPTH_COMPONENT24, w, h)
            GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_DEPTH_ATTACHMENT, GL.GL_RENDERBUFFER, self.rb)

    def bind(self):
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        GL.glViewport(0, 0, self.w, self.h)

    def delete(self):
        GL.glDeleteFramebuffers(1, [self.fbo])
        GL.glDeleteTextures([self.tex])
        if self.rb:
            GL.glDeleteRenderbuffers(1, [self.rb])


class MSTarget:
    """Multisampled colour + depth framebuffer, resolved into a Target."""

    def __init__(self, w, h, samples=4):
        self.w, self.h = w, h
        self.fbo = GL.glGenFramebuffers(1)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        self.crb, self.drb = GL.glGenRenderbuffers(2)
        GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, self.crb)
        GL.glRenderbufferStorageMultisample(GL.GL_RENDERBUFFER, samples, GL.GL_RGBA16F, w, h)
        GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_RENDERBUFFER, self.crb)
        GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, self.drb)
        GL.glRenderbufferStorageMultisample(GL.GL_RENDERBUFFER, samples, GL.GL_DEPTH_COMPONENT24, w, h)
        GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_DEPTH_ATTACHMENT, GL.GL_RENDERBUFFER, self.drb)
        self.resolved = Target(w, h)

    def bind(self):
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        GL.glViewport(0, 0, self.w, self.h)

    def resolve(self):
        GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, self.fbo)
        GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, self.resolved.fbo)
        GL.glBlitFramebuffer(0, 0, self.w, self.h, 0, 0, self.w, self.h, GL.GL_COLOR_BUFFER_BIT, GL.GL_NEAREST)
        return self.resolved

    def delete(self):
        GL.glDeleteFramebuffers(1, [self.fbo])
        GL.glDeleteRenderbuffers(2, [self.crb, self.drb])
        self.resolved.delete()


# ---------------------------------------------------------------- matrices (row-major, column vectors)
def perspective(fovy_deg, aspect, near, far):
    f = 1.0 / np.tan(np.radians(fovy_deg) / 2)
    m = np.zeros((4, 4), np.float32)
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = 2 * far * near / (near - far)
    m[3, 2] = -1
    return m


def ortho(l, r, b, t, n=-1, f=1):
    m = np.eye(4, dtype=np.float32)
    m[0, 0] = 2 / (r - l)
    m[1, 1] = 2 / (t - b)
    m[2, 2] = -2 / (f - n)
    m[0, 3] = -(r + l) / (r - l)
    m[1, 3] = -(t + b) / (t - b)
    m[2, 3] = -(f + n) / (f - n)
    return m


def look_at(eye, target, up=(0, 1, 0)):
    eye, target, up = (np.asarray(v, np.float32) for v in (eye, target, up))
    f = target - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.eye(4, dtype=np.float32)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


def translate(x, y, z):
    m = np.eye(4, dtype=np.float32)
    m[:3, 3] = (x, y, z)
    return m


def scale(s):
    m = np.eye(4, dtype=np.float32)
    m[0, 0] = m[1, 1] = m[2, 2] = s
    return m


def rot_x(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1]], np.float32)


def rot_y(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]], np.float32)


def rot_z(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], np.float32)
