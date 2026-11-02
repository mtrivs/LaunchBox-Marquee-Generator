"""Byte-bounded image storage shared by preview and batch renderers."""
from collections import OrderedDict
from collections.abc import MutableMapping
from threading import RLock

class ImageCache(MutableMapping):
    """Thread-safe LRU storage measured by decoded pixel bytes.

    Oversized images remain renderable but are not retained. Returned images are
    shared read-only values: renderers must copy before changing pixels.
    """
    def __init__(self,max_bytes=128*1024*1024):
        self.data=OrderedDict(); self.bytes=0; self.max_bytes=int(max_bytes); self.lock=RLock()
    @staticmethod
    def size(image): return image.width*image.height*len(image.getbands())
    def __getitem__(self,key):
        """Return an image and promote it to the most recently used position.
        """
        with self.lock:
            value=self.data[key]; self.data.move_to_end(key); return value
    def __setitem__(self,key,value):
        """Store an image, evicting older buffers until the byte budget fits.
        """
        with self.lock:
            if key in self.data: self.bytes-=self.size(self.data.pop(key))
            size=self.size(value)
            if size>self.max_bytes: return
            while self.data and self.bytes+size>self.max_bytes:
                _,old=self.data.popitem(last=False); self.bytes-=self.size(old)
            self.data[key]=value; self.bytes+=size
    def __delitem__(self,key):
        with self.lock: self.bytes-=self.size(self.data.pop(key))
    def __iter__(self):
        with self.lock: return iter(list(self.data))
    def __len__(self):
        with self.lock: return len(self.data)
    def set_budget(self,max_bytes):
        """Apply a new budget immediately, including eviction of retained images.
        """
        with self.lock:
            self.max_bytes=int(max_bytes)
            while self.data and self.bytes>self.max_bytes:
                _,old=self.data.popitem(last=False); self.bytes-=self.size(old)
    def clear(self):
        """Release all retained image references and reset byte accounting.
        """
        with self.lock: self.data.clear(); self.bytes=0
