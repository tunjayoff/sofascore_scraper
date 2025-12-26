"""
SofaScore Scraper uygulaması için özel hata sınıfları.
"""


class SofaScoreScraperError(Exception):
    """Uygulama için temel hata sınıfı."""
    
    def __init__(self, message: str = "Sofascore Scraper'da bir hata oluştu"):
        self.message = message
        super().__init__(self.message)


class ConfigError(SofaScoreScraperError):
    """Yapılandırma hatası için özel sınıf."""
    
    def __init__(self, message: str = "Yapılandırma hatası oluştu"):
        super().__init__(message)


class APIError(SofaScoreScraperError):
    """API isteklerinde oluşan hatalar için özel sınıf."""
    
    def __init__(self, message: str = "API isteği sırasında bir hata oluştu", status_code: int = None):
        self.status_code = status_code
        status_info = f" (Durum Kodu: {status_code})" if status_code else ""
        super().__init__(message + status_info)


class RateLimitError(APIError):
    """Rate limiting hatası için özel sınıf."""
    
    def __init__(self, wait_time: int = None):
        self.wait_time = wait_time
        message = "API istek limiti aşıldı"
        if wait_time:
            message += f", {wait_time} saniye bekleniyor"
        super().__init__(message, 429)

class ResourceNotFoundError(APIError):
    """İstenen kaynak bulunamadığında (404) oluşan hata."""
    
    def __init__(self, message: str = "Kaynak bulunamadı"):
        super().__init__(message, 404)


class DataNotFoundError(SofaScoreScraperError):
    """Veri bulunamadığında oluşan hatalar için özel sınıf."""
    
    def __init__(self, data_type: str = "Veri", identifier: str = None):
        message = f"{data_type} bulunamadı"
        if identifier:
            message += f": {identifier}"
        super().__init__(message)


class DataParsingError(SofaScoreScraperError):
    """Veri ayrıştırma hatası için özel sınıf."""
    
    def __init__(self, message: str = "Veri ayrıştırma hatası oluştu"):
        super().__init__(message)


class NetworkError(SofaScoreScraperError):
    """Ağ hatası için özel sınıf."""
    
    def __init__(self, message: str = "Ağ bağlantısı sırasında bir hata oluştu"):
        super().__init__(message)


class ValidationError(SofaScoreScraperError):
    """Veri doğrulama hatası için özel sınıf."""
    
    def __init__(self, field: str = None, message: str = "Veri doğrulama hatası"):
        if field:
            message = f"{field} alanı için {message}"
        super().__init__(message) 