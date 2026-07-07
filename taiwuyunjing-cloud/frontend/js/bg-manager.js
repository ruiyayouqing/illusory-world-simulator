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
    updateForTheme(themeName) {
        const gradient = this._themeGradients[themeName] || this._themeGradients.obsidian;
        const vignette = this._themeVignettes[themeName] || this._themeVignettes.obsidian;
        // 更新所有背景层
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
        return new Promise((resolve) => {
            const img = new Image();
            img.onload = () => {
                this.bgCache.set(imageUrl, img);
                resolve(imageUrl);
            };
            img.onerror = () => resolve(null);
            img.src = imageUrl;
        });
    },

    setBackground(imageUrl, transition = true, duration = null) {
        if (!this.bgLayers.length) this.init();
        
        if (this.currentBg === imageUrl) return;
        
        const transDur = duration || this.transitionDuration;

        if (!transition || !imageUrl) {
            const layer = this.bgLayers[this.activeLayer];
            if (imageUrl) {
                layer.style.backgroundImage = `url(${imageUrl})`;
                layer.classList.add('bg-image');
                this.bgLayers[1 - this.activeLayer].style.opacity = '0';
            } else {
                layer.style.backgroundImage = '';
                layer.classList.remove('bg-image');
                this._setDefaultGradient(layer);
                this.bgLayers[1 - this.activeLayer].style.opacity = '0';
            }
            this.currentBg = imageUrl;
            return;
        }

        this.preload(imageUrl).then(() => {
            const nextLayer = this.bgLayers[1 - this.activeLayer];
            const currentLayer = this.bgLayers[this.activeLayer];
            
            nextLayer.style.transitionDuration = `${transDur}ms`;
            currentLayer.style.transitionDuration = `${transDur}ms`;
            
            nextLayer.style.backgroundImage = `url(${imageUrl})`;
            nextLayer.classList.add('bg-image');
            
            requestAnimationFrame(() => {
                nextLayer.style.opacity = '1';
                currentLayer.style.opacity = '0';
            });
            
            setTimeout(() => {
                currentLayer.style.backgroundImage = '';
                currentLayer.classList.remove('bg-image');
                this.currentBg = imageUrl;
                this.activeLayer = 1 - this.activeLayer;
            }, transDur);
        });
    },

    setGradientBg(type = 'default', transition = true) {
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
        };
        
        const gradient = gradients[type] || gradients.default;
        
        if (transition) {
            const nextLayer = this.bgLayers[1 - this.activeLayer];
            const currentLayer = this.bgLayers[this.activeLayer];
            
            nextLayer.style.background = gradient;
            nextLayer.classList.remove('bg-image');
            
            requestAnimationFrame(() => {
                nextLayer.style.opacity = '1';
                currentLayer.style.opacity = '0';
            });
            
            setTimeout(() => {
                currentLayer.style.background = gradient;
                currentLayer.classList.remove('bg-image');
                this.activeLayer = 1 - this.activeLayer;
            }, this.transitionDuration);
        } else {
            this.bgLayers.forEach(layer => {
                layer.style.background = gradient;
                layer.classList.remove('bg-image');
            });
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
});
