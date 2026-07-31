const BGManager = {
    currentBg: null,
    bgCache: new Map(),
    bgLayers: [],
    activeLayer: 0,
    transitionDuration: 1200,
    sceneBgs: new Map(),

    // [Bug] 主题感知：浅色主题使用柔和渐变，深色主题使用暗色渐变
    _themeGradients: {
        // 深色主题
        obsidian:  'linear-gradient(135deg, #0a090c 0%, #15121a 50%, #0a090c 100%)',
        midnight:  'linear-gradient(135deg, #080c1a 0%, #101830 50%, #080c1a 100%)',
        crimson:   'linear-gradient(135deg, #1a0a0a 0%, #2a1010 50%, #1a0a0a 100%)',
        forest:    'linear-gradient(135deg, #0a1408 0%, #152510 50%, #0a1408 100%)',
        // 浅色主题
        parchment: 'linear-gradient(135deg, #f0e8d8 0%, #e8dcc8 50%, #f0e8d8 100%)',
        sakura:    'linear-gradient(135deg, #f8e8ec 0%, #f0dce2 50%, #f8e8ec 100%)',
        mint:      'linear-gradient(135deg, #e8f4ec 0%, #dceee4 50%, #e8f4ec 100%)',
        ivory:     'linear-gradient(135deg, #f4f4f4 0%, #e8e8e8 50%, #f4f4f4 100%)',
    },

    // [Bug] 主题感知暗角：浅色主题使用浅暗角，深色主题使用深暗角
    _themeVignettes: {
        obsidian:  'radial-gradient(ellipse at center, transparent 30%, rgba(0,0,0,0.6) 100%)',
        midnight:  'radial-gradient(ellipse at center, transparent 30%, rgba(0,0,0,0.6) 100%)',
        crimson:   'radial-gradient(ellipse at center, transparent 30%, rgba(0,0,0,0.6) 100%)',
        forest:    'radial-gradient(ellipse at center, transparent 30%, rgba(0,0,0,0.6) 100%)',
        parchment: 'radial-gradient(ellipse at center, transparent 30%, rgba(0,0,0,0.08) 100%)',
        sakura:    'radial-gradient(ellipse at center, transparent 30%, rgba(0,0,0,0.06) 100%)',
        mint:      'radial-gradient(ellipse at center, transparent 30%, rgba(0,0,0,0.06) 100%)',
        ivory:     'radial-gradient(ellipse at center, transparent 30%, rgba(0,0,0,0.05) 100%)',
    },

    init() {
        this.bgLayer = document.getElementById('bg-layer');
        this.vignette = document.getElementById('bg-vignette');

        this.bgLayers = [
            this.bgLayer,
            this._createSecondLayer()
        ];
        this.activeLayer = 0;

        this._applyVignette();

        // [NovelRoleplay] 二级/三级页面背景图片列表（从后端动态获取）
        this._subpageBgImages = [];
        this._lastSubpageBg = null;
        this._loadSubpageBgImages();
    },

    _createSecondLayer() {
        const layer = document.createElement('div');
        layer.id = 'bg-layer-2';
        var theme = (typeof window !== 'undefined' && window._currentThemeName) || 'obsidian';
        var initGradient = this._themeGradients[theme] || this._themeGradients.obsidian;
        layer.style.cssText = `
            position: fixed;
            inset: 0;
            z-index: 0;
            background: ${initGradient};
            background-size: cover;
            background-position: center;
            background-repeat: no-repeat;
            opacity: 0;
            transition: opacity 1.2s cubic-bezier(0.4, 0, 0.2, 1);
        `;
        this.bgLayer.parentNode.insertBefore(layer, this.bgLayer.nextSibling);
        return layer;
    },

    _applyVignette() {
        if (this.vignette) {
            this.vignette.style.cssText = `
                position: fixed;
                inset: 0;
                z-index: 1;
                pointer-events: none;
                background: radial-gradient(ellipse at center, transparent 30%, rgba(0,0,0,0.6) 100%);
            `;
        }
    },

    // [Bug] 主题切换时更新背景层和暗角，使其匹配当前主题配色
    // 【关键修复】如果当前是图片背景（currentBg 为图片 URL 或 _imageBgForced 标记），
    // 不要被主题渐变覆盖，只更新暗角。只有明确调用 setGradientBg 时才用渐变覆盖。
    updateForTheme(themeName) {
        const gradient = this._themeGradients[themeName] || this._themeGradients.obsidian;
        const vignette = this._themeVignettes[themeName] || this._themeVignettes.obsidian;

        // 如果当前是图片背景，不覆盖图片，只同步暗角
        const isImageBg = (this.currentBg && this.currentBg.startsWith('/images/'))
            || this._imageBgForced;
        if (isImageBg) {
            // 图片背景保持不变，只更新暗角
            if (this.vignette) {
                this.vignette.style.background = vignette;
            }
            return;
        }

        // 非图片背景（渐变模式）才更新背景层
        this.bgLayers.forEach(layer => {
            if (layer) {
                layer.style.background = gradient;
                layer.classList.remove('bg-image');
            }
        });
        // 更新暗角
        if (this.vignette) {
            this.vignette.style.background = vignette;
        }
        this.currentBg = null;
    },

    preload(imageUrl) {
        if (this.bgCache.has(imageUrl)) return Promise.resolve(imageUrl);
        return new Promise((resolve, reject) => {
            const img = new Image();
            img.onload = () => {
                this.bgCache.set(imageUrl, img);
                resolve(imageUrl);
            };
            img.onerror = () => {
                console.warn('[BG] preload failed:', imageUrl);
                reject(new Error('Failed to load image: ' + imageUrl));
            };
            img.src = imageUrl;
        });
    },

    setBackground(imageUrl, transition = true, duration = null) {
        if (!this.bgLayers.length) this.init();
        
        if (!transition || !imageUrl) {
            this._forceSetBackground(imageUrl);
            return;
        }

        if (this.currentBg === imageUrl) return;
        
        const transDur = duration || this.transitionDuration;

        this._imageBgForced = true;  // 标记：进入图片背景模式
        this.preload(imageUrl).then((loadedUrl) => {
            const nextLayer = this.bgLayers[1 - this.activeLayer];
            const currentLayer = this.bgLayers[this.activeLayer];
            
            nextLayer.style.transitionDuration = `${transDur}ms`;
            currentLayer.style.transitionDuration = `${transDur}ms`;
            
            nextLayer.style.backgroundImage = `url("${loadedUrl}")`;
            nextLayer.style.backgroundSize = 'cover';
            nextLayer.style.backgroundPosition = 'center center';
            nextLayer.style.backgroundRepeat = 'no-repeat';
            nextLayer.classList.add('bg-image');
            
            requestAnimationFrame(() => {
                nextLayer.style.opacity = '1';
                currentLayer.style.opacity = '0';
            });
            
            setTimeout(() => {
                currentLayer.style.backgroundImage = '';
                currentLayer.style.backgroundSize = '';
                currentLayer.style.backgroundPosition = '';
                currentLayer.classList.remove('bg-image');
                this.currentBg = loadedUrl;
                this.activeLayer = 1 - this.activeLayer;
            }, transDur);
        }).catch((err) => {
            console.warn('[BG] setBackground failed:', err);
            this._imageBgForced = false;
        });
    },

    _forceSetBackground(imageUrl) {
        const layer = this.bgLayers[this.activeLayer];
        const otherLayer = this.bgLayers[1 - this.activeLayer];
        otherLayer.style.opacity = '0';
        
        if (imageUrl) {
            this._imageBgForced = true;
            const img = new Image();
            img.onload = () => {
                this._imageBgForced = true;
                layer.style.backgroundImage = `url("${imageUrl}")`;
                layer.style.backgroundSize = 'cover';
                layer.style.backgroundPosition = 'center center';
                layer.style.backgroundRepeat = 'no-repeat';
                layer.style.opacity = '1';
                layer.classList.add('bg-image');
                this.currentBg = imageUrl;
            };
            img.onerror = () => {
                console.warn('[BG] _forceSetBackground failed to load:', imageUrl);
                this._imageBgForced = false;
                layer.style.backgroundImage = '';
                this._setDefaultGradient(layer);
                this.currentBg = null;
            };
            img.src = imageUrl;
        } else {
            this._imageBgForced = false;
            layer.style.backgroundImage = '';
            layer.style.backgroundSize = '';
            layer.style.backgroundPosition = '';
            layer.classList.remove('bg-image');
            this._setDefaultGradient(layer);
            this.currentBg = null;
        }
    },

    forceSetHomeBg() {
        if (!this.bgLayers.length) this.init();
        const url = '/images/ditu.png';
        this._imageBgForced = true;  // 标记：当前是图片背景模式，防止被主题渐变覆盖
        this.preload(url).then(() => {
            this._imageBgForced = true;
            this.bgLayers.forEach((layer, idx) => {
                layer.style.transitionDuration = '0ms';
                layer.style.backgroundImage = `url("${url}")`;
                layer.style.backgroundSize = 'cover';
                layer.style.backgroundPosition = 'center center';
                layer.style.backgroundRepeat = 'no-repeat';
                layer.classList.add('bg-image');
            });
            this.bgLayers[0].style.opacity = '1';
            this.bgLayers[1].style.opacity = '0';
            this.activeLayer = 0;
            this.currentBg = url;
        }).catch((err) => {
            console.warn('[BG] forceSetHomeBg failed:', err);
            this._imageBgForced = false;
        });
    },

    setGradientBg(type = 'default', transition = true) {
        this._imageBgForced = false;  // 明确切换到渐变模式，清除图片背景标记
        const gradients = {
            default: 'linear-gradient(135deg, #0a090c 0%, #15121a 50%, #0a090c 100%)',
            warm: 'linear-gradient(135deg, #1a1208 0%, #2a1f10 50%, #1a1208 100%)',
            cold: 'linear-gradient(135deg, #080c1a 0%, #101830 50%, #080c1a 100%)',
            forest: 'linear-gradient(135deg, #0a1408 0%, #152510 50%, #0a1408 100%)',
            danger: 'linear-gradient(135deg, #1a0808 0%, #2a1010 50%, #1a0808 100%)',
            mystery: 'linear-gradient(135deg, #0f0a18 0%, #1a1030 50%, #0f0a18 100%)',
            royal: 'linear-gradient(135deg, #14100a 0%, #2a2010 40%, #1a1508 60%, #14100a 100%)',
            dawn: 'linear-gradient(135deg, #1a1018 0%, #2a1820 30%, #3a2020 60%, #2a1818 100%)',
            // [Bug] 浅色主题渐变 — 进入游戏时使用主题对应的柔和渐变
            'theme-parchment': 'linear-gradient(135deg, #f0e8d8 0%, #e8dcc8 50%, #f0e8d8 100%)',
            'theme-sakura':    'linear-gradient(135deg, #f8e8ec 0%, #f0dce2 50%, #f8e8ec 100%)',
            'theme-mint':      'linear-gradient(135deg, #e8f4ec 0%, #dceee4 50%, #e8f4ec 100%)',
            'theme-ivory':     'linear-gradient(135deg, #f4f4f4 0%, #e8e8e8 50%, #f4f4f4 100%)',
            // [Bug] 深色主题渐变 — 进入游戏时使用主题对应的底色方案
            'theme-obsidian':  'linear-gradient(135deg, #0a090c 0%, #15121a 50%, #0a090c 100%)',
            'theme-midnight':  'linear-gradient(135deg, #080c1a 0%, #101830 50%, #080c1a 100%)',
            'theme-crimson':   'linear-gradient(135deg, #1a0a0a 0%, #2a1010 50%, #1a0a0a 100%)',
            'theme-forest':    'linear-gradient(135deg, #0a1408 0%, #152510 50%, #0a1408 100%)',
        };
        
        const gradient = gradients[type] || gradients.default;
        
        if (transition) {
            const nextLayer = this.bgLayers[1 - this.activeLayer];
            const currentLayer = this.bgLayers[this.activeLayer];
            
            nextLayer.style.transitionDuration = `${this.transitionDuration}ms`;
            currentLayer.style.transitionDuration = `${this.transitionDuration}ms`;
            nextLayer.style.backgroundImage = '';
            nextLayer.style.background = gradient;
            nextLayer.style.backgroundSize = '';
            nextLayer.style.backgroundPosition = '';
            nextLayer.classList.remove('bg-image');
            
            requestAnimationFrame(() => {
                nextLayer.style.opacity = '1';
                currentLayer.style.opacity = '0';
            });
            
            setTimeout(() => {
                currentLayer.style.backgroundImage = '';
                currentLayer.style.background = gradient;
                currentLayer.style.backgroundSize = '';
                currentLayer.style.backgroundPosition = '';
                currentLayer.classList.remove('bg-image');
                this.activeLayer = 1 - this.activeLayer;
            }, this.transitionDuration);
        } else {
            this.bgLayers.forEach((layer, idx) => {
                layer.style.transitionDuration = '0ms';
                layer.style.backgroundImage = '';
                layer.style.background = gradient;
                layer.style.backgroundSize = '';
                layer.style.backgroundPosition = '';
                layer.classList.remove('bg-image');
            });
            this.bgLayers[0].style.opacity = '1';
            this.bgLayers[1].style.opacity = '0';
            this.activeLayer = 0;
        }
        
        this.currentBg = null;
    },

    _setDefaultGradient(layer) {
        // [Bug] 使用当前主题对应的渐变，而非硬编码深色
        var theme = (typeof window !== 'undefined' && window._currentThemeName) || 'obsidian';
        layer.style.background = this._themeGradients[theme] || this._themeGradients.obsidian;
    },

    setSceneBackground(sceneType, customUrl = null) {
        if (customUrl) {
            this.setBackground(customUrl);
            return;
        }
        
        const sceneGradients = {
            'indoor': 'warm',
            'outdoor': 'default',
            'forest': 'forest',
            'battle': 'danger',
            'mystery': 'mystery',
            'palace': 'royal',
            'night': 'cold',
            'dawn': 'dawn'
        };
        
        const gradType = sceneGradients[sceneType] || 'default';
        this.setGradientBg(gradType);
    },

    // [NovelRoleplay] 从后端加载二级/三级页面背景图片列表
    async _loadSubpageBgImages() {
        try {
            const response = await fetch('/api/bg-images');
            const data = await response.json();
            if (data && data.images && Array.isArray(data.images)) {
                this._subpageBgImages = data.images;
                console.log('[BG] 加载二级页面背景图列表: ' + this._subpageBgImages.length + ' 张');
            }
        } catch (e) {
            console.warn('[BG] 加载背景图列表失败:', e);
        }
    },

    // [NovelRoleplay] 重新加载背景图列表（用于检测新添加的图片）
    // 清空洗牌队列，让新图片能立即加入轮换
    async reloadSubpageBgImages() {
        await this._loadSubpageBgImages();
        this._shuffledBgQueue = [];  // 清空队列，下次 setSubpageBackground 会重新洗牌
    },

    // [NovelRoleplay] 设置二级/三级页面背景（使用洗牌算法确保每张图片都会出现）
    setSubpageBackground() {
        if (!this._subpageBgImages || this._subpageBgImages.length === 0) {
            // 如果没有图片，回退到默认渐变
            this.setGradientBg('theme-parchment');
            return;
        }

        // 使用洗牌算法（Fisher-Yates）确保每张图片都会出现一轮
        // 当队列空了或第一次调用时，重新洗牌
        if (!this._shuffledBgQueue || this._shuffledBgQueue.length === 0) {
            this._shuffledBgQueue = this._subpageBgImages.slice();  // 复制一份
            // Fisher-Yates 洗牌
            for (let i = this._shuffledBgQueue.length - 1; i > 0; i--) {
                const j = Math.floor(Math.random() * (i + 1));
                [this._shuffledBgQueue[i], this._shuffledBgQueue[j]] =
                    [this._shuffledBgQueue[j], this._shuffledBgQueue[i]];
            }
            // 避免新一轮第一张和上一轮最后一张相同
            if (this._shuffledBgQueue.length > 1 &&
                this._shuffledBgQueue[0] === this._lastSubpageBg) {
                // 把第一张和第二张交换
                [this._shuffledBgQueue[0], this._shuffledBgQueue[1]] =
                    [this._shuffledBgQueue[1], this._shuffledBgQueue[0]];
            }
            console.log('[BG] 背景图队列已重新洗牌，共 ' + this._shuffledBgQueue.length + ' 张');
        }

        // 从队列头部取一张
        const selected = this._shuffledBgQueue.shift();
        this._lastSubpageBg = selected;

        this._imageBgForced = true;
        this.preload(selected).then(() => {
            this.setBackground(selected);
        }).catch(() => {
            // 加载失败，回退到默认渐变
            this.setGradientBg('theme-parchment');
        });
    },

    // [NovelRoleplay] 启动二级/三级页面背景定时轮换（默认 30 秒一张）
    startSubpageBgRotation(intervalMs = 30000) {
        this.stopSubpageBgRotation();
        this._subpageBgTimer = setInterval(() => {
            // 只在游戏页面（二级/三级）轮换，首页不轮换
            var homeEl = document.getElementById('home');
            if (homeEl && homeEl.style.display === 'flex') {
                return;
            }
            this.setSubpageBackground();
        }, intervalMs);
        console.log('[BG] 二级页面背景轮换已启动，间隔 ' + intervalMs + 'ms');
    },

    // [NovelRoleplay] 停止二级/三级页面背景定时轮换
    stopSubpageBgRotation() {
        if (this._subpageBgTimer) {
            clearInterval(this._subpageBgTimer);
            this._subpageBgTimer = null;
        }
    },

    flash(color = 'rgba(212,175,55,0.3)', duration = 300) {
        const flash = document.createElement('div');
        flash.style.cssText = `
            position: fixed;
            inset: 0;
            z-index: 50;
            background: ${color};
            pointer-events: none;
            opacity: 1;
            transition: opacity ${duration}ms ease-out;
        `;
        document.body.appendChild(flash);
        
        requestAnimationFrame(() => {
            flash.style.opacity = '0';
        });
        
        setTimeout(() => flash.remove(), duration);
    },

    fadeToBlack(duration = 800) {
        return new Promise(resolve => {
            const overlay = document.createElement('div');
            overlay.style.cssText = `
                position: fixed;
                inset: 0;
                z-index: 100;
                background: #000;
                pointer-events: none;
                opacity: 0;
                transition: opacity ${duration}ms ease;
            `;
            document.body.appendChild(overlay);
            
            requestAnimationFrame(() => {
                overlay.style.opacity = '1';
            });
            
            setTimeout(() => {
                this._fadeOverlay = overlay;
                resolve();
            }, duration);
        });
    },

    fadeFromBlack(duration = 800) {
        return new Promise(resolve => {
            if (!this._fadeOverlay) {
                resolve();
                return;
            }
            this._fadeOverlay.style.opacity = '0';
            setTimeout(() => {
                if (this._fadeOverlay) {
                    this._fadeOverlay.remove();
                    this._fadeOverlay = null;
                }
                resolve();
            }, duration);
        });
    }
};

document.addEventListener('DOMContentLoaded', () => {
    BGManager.init();

    // [Bug] 主页背景图间歇性消失修复：
    // 原因1：原先只有 img.onload，无 onerror，图片加载失败时静默无动作
    // 原因2：30ms 太短，与其他初始化代码（主题设置等）竞争，导致 backgroundImage 被清空
    // 原因3：多重代码路径（bg-manager / novel_roleplay / game.js）时序不同，互相覆盖
    // 修复：用 BGManager.forceSetHomeBg() 统一入口（preload + Promise 缓存），
    //       加 onerror 重试 + 1 秒保底检查
    let _homeBgReady = false;
    let _retryCount = 0;
    const _maxRetries = 3;

    function _trySetHomeBg(delay) {
        setTimeout(() => {
            if (_homeBgReady) return;
            BGManager.forceSetHomeBg();
            // 验证：preload 完成后 currentBg 应为 url
            // 但 forceSetHomeBg 内部 preload 是异步的，需要再延迟检查
            setTimeout(() => {
                if (BGManager.currentBg === '/images/ditu.png'
                    && BGManager.bgLayers[0]
                    && BGManager.bgLayers[0].style.backgroundImage) {
                    _homeBgReady = true;
                } else if (_retryCount < _maxRetries) {
                    _retryCount++;
                    console.warn('[BG] Home bg not set, retry ' + _retryCount);
                    _trySetHomeBg(200 * _retryCount);
                }
            }, 500);
        }, delay);
    }

    // 首次尝试：用 requestAnimationFrame 确保浏览器完成布局后再加载
    requestAnimationFrame(() => {
        _trySetHomeBg(0);
    });

    // 保底：2 秒后如果还没成功，强制再试一次（应对极端情况）
    setTimeout(() => {
        if (!_homeBgReady) {
            console.warn('[BG] Home bg fallback trigger');
            BGManager.forceSetHomeBg();
        }
    }, 2000);
});
